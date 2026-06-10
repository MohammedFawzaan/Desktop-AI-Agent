import { useState, useEffect, useRef, useCallback } from 'react';

const IDLE       = 'idle';
const LISTENING  = 'listening';
const PROCESSING = 'processing';
const SPEAKING   = 'speaking';

const SILENCE_THRESHOLD = 18;     // avg amplitude below this = silence (raised for noisy mics)
const SILENCE_DELAY_MS  = 1500;
const MAX_RECORDING_MS  = 15000;  // hard cap so recording always stops eventually

export default function App() {
    const [messages, setMessages]             = useState([{ text: "Hello! I'm Friday, your AI Desktop Agent. How can I help you today?", sender: "agent" }]);
    const [input, setInput]                   = useState("");
    const [voiceMode, setVoiceMode]           = useState(false);
    const [voiceState, setVoiceState]         = useState(IDLE);
    const [liveTranscript, setLiveTranscript] = useState("");

    const isListenerAttached = useRef(false);
    const recorderRef        = useRef(null);
    const silenceTimerRef    = useRef(null);
    const animFrameRef       = useRef(null);
    const voiceModeRef       = useRef(false);
    const voiceStateRef      = useRef(IDLE);
    const cancelledRef       = useRef(false);
    const messagesEndRef     = useRef(null);

    const setVState = (s) => { voiceStateRef.current = s; setVoiceState(s); };

    const speak = useCallback((text) => {
        return new Promise((resolve) => {
            window.speechSynthesis.cancel();
            const utt = new SpeechSynthesisUtterance(text);
            utt.onend = resolve; utt.onerror = resolve;
            window.speechSynthesis.speak(utt);
        });
    }, []);

    const cancelVoice = useCallback(() => {
        cancelledRef.current = true;
        cancelAnimationFrame(animFrameRef.current);
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
        if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
        window.speechSynthesis.cancel();
        setLiveTranscript('');
        setVState(IDLE);
    }, []);

    const startListening = useCallback(() => {
        cancelledRef.current = false;
        setVState(LISTENING);
        setLiveTranscript('Listening...');

        navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
            const audioCtx = new AudioContext();
            audioCtx.resume();   // may start suspended
            const source   = audioCtx.createMediaStreamSource(stream);
            const analyser = audioCtx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);

            const recorder = new MediaRecorder(stream);
            const chunks   = [];
            recorderRef.current = recorder;

            // Hard cap: stop after MAX_RECORDING_MS no matter what
            const maxTimer = setTimeout(() => {
                if (recorder.state === 'recording') recorder.stop();
            }, MAX_RECORDING_MS);

            recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };

            recorder.onstop = async () => {
                cancelAnimationFrame(animFrameRef.current);
                clearTimeout(silenceTimerRef.current);
                clearTimeout(maxTimer);
                silenceTimerRef.current = null;
                stream.getTracks().forEach(t => t.stop());
                audioCtx.close();

                if (cancelledRef.current || !voiceModeRef.current) return;

                setLiveTranscript('Transcribing...');
                setVState(PROCESSING);

                try {
                    const blob   = new Blob(chunks, { type: 'audio/webm' });
                    const buffer = await blob.arrayBuffer();
                    const bytes  = new Uint8Array(buffer);
                    let binary   = '';
                    for (let i = 0; i < bytes.length; i += 8192)
                        binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
                    const base64 = btoa(binary);
                    const text   = await window.api.transcribeAudio(base64, 'audio/webm');

                    if (cancelledRef.current) return;
                    setLiveTranscript('');
                    if (text) {
                        setMessages(prev => [...prev, { text, sender: 'user' }]);
                        window.api.sendMessage(text);
                    } else {
                        setVState(IDLE);
                    }
                } catch (err) {
                    console.error('Transcription error:', err);
                    if (!cancelledRef.current) setVState(IDLE);
                }
            };

            const checkSilence = () => {
                const data = new Uint8Array(analyser.frequencyBinCount);
                analyser.getByteFrequencyData(data);
                const avg = data.reduce((a, b) => a + b, 0) / data.length;
                if (avg < SILENCE_THRESHOLD) {
                    if (!silenceTimerRef.current)
                        silenceTimerRef.current = setTimeout(() => {
                            if (recorder.state === 'recording') recorder.stop();
                        }, SILENCE_DELAY_MS);
                } else {
                    clearTimeout(silenceTimerRef.current);
                    silenceTimerRef.current = null;
                    setLiveTranscript('Hearing you...');
                }
                if (recorder.state === 'recording')
                    animFrameRef.current = requestAnimationFrame(checkSilence);
            };

            recorder.start();
            animFrameRef.current = requestAnimationFrame(checkSilence);
        }).catch((err) => {
            console.error('Microphone error:', err);
            setVState(IDLE);
        });
    }, []);

    useEffect(() => {
        if (!isListenerAttached.current) {
            window.api.onResponse(async (response) => {
                // Always render the agent's reply, even if voice was cancelled
                // (the command already ran on the backend).
                setMessages(prev => [...prev, { text: response, sender: 'agent' }]);

                // Only speak it if we're in voice mode and the user didn't cancel.
                if (voiceModeRef.current && !cancelledRef.current) {
                    setVState(SPEAKING);
                    await speak(response);
                    if (!cancelledRef.current) setVState(IDLE);
                } else {
                    setVState(IDLE);
                }
            });
            isListenerAttached.current = true;
        }
    }, [speak]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, liveTranscript]);

    const toggleVoiceMode = () => {
        const next = !voiceMode;
        setVoiceMode(next);
        voiceModeRef.current = next;
        if (!next) cancelVoice();
        else { cancelledRef.current = false; setVState(IDLE); }
    };

    const sendTextMessage = () => {
        if (!input.trim() || voiceState === PROCESSING) return;
        cancelledRef.current = false;   // text sends are never "cancelled"
        setMessages(prev => [...prev, { text: input, sender: 'user' }]);
        window.api.sendMessage(input);
        setInput('');
        setVState(PROCESSING);
    };

    const isProcessing = voiceState === PROCESSING;

    const orbColor = {
        [IDLE]:       '#94a3b8',
        [LISTENING]:  '#ef4444',
        [PROCESSING]: '#f59e0b',
        [SPEAKING]:   '#22c55e',
    }[voiceState];

    const orbIcon = {
        [IDLE]:       '🎤',
        [LISTENING]:  '🎤',
        [PROCESSING]: '⏳',
        [SPEAKING]:   '🔊',
    }[voiceState];

    const statusText = {
        [IDLE]:       'Ready — press Start to speak',
        [LISTENING]:  liveTranscript,
        [PROCESSING]: 'Processing...',
        [SPEAKING]:   'Speaking...',
    }[voiceState];

    return (
        <div style={{
            display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw',
            background: '#f1f5f9', fontFamily: "'Segoe UI', sans-serif", overflow: 'hidden',
        }}>
            {/* Header */}
            <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '0 28px', height: '64px', flexShrink: 0,
                background: '#ffffff', borderBottom: '1px solid #e2e8f0',
                boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{
                        width: '38px', height: '38px', borderRadius: '50%',
                        background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: '18px',
                    }}>🤖</div>
                    <div>
                        <div style={{ fontWeight: '700', fontSize: '16px', color: '#0f172a' }}>Friday</div>
                        <div style={{ fontSize: '11px', color: '#64748b' }}>AI Desktop Agent</div>
                    </div>
                </div>

                <button onClick={toggleVoiceMode} style={{
                    display: 'flex', alignItems: 'center', gap: '7px',
                    padding: '8px 18px',
                    background: voiceMode ? '#fef2f2' : '#eff6ff',
                    color: voiceMode ? '#dc2626' : '#2563eb',
                    border: `1.5px solid ${voiceMode ? '#fca5a5' : '#bfdbfe'}`,
                    borderRadius: '8px', cursor: 'pointer',
                    fontWeight: '600', fontSize: '13px',
                    transition: 'all 0.2s',
                }}>
                    <span>{voiceMode ? '🎤' : '⌨️'}</span>
                    {voiceMode ? 'Voice On' : 'Text Mode'}
                </button>
            </div>

            {/* Messages */}
            <div style={{
                flex: 1, overflowY: 'auto', padding: '24px 28px',
                display: 'flex', flexDirection: 'column', gap: '12px',
            }}>
                {messages.map((m, i) => (
                    <div key={i} style={{
                        display: 'flex',
                        justifyContent: m.sender === 'user' ? 'flex-end' : 'flex-start',
                    }}>
                        {m.sender === 'agent' && (
                            <div style={{
                                width: '32px', height: '32px', borderRadius: '50%', flexShrink: 0,
                                background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                fontSize: '14px', marginRight: '10px', alignSelf: 'flex-end',
                            }}>🤖</div>
                        )}
                        <div style={{
                            maxWidth: '68%', padding: '11px 16px', borderRadius: '16px',
                            borderBottomRightRadius: m.sender === 'user' ? '4px' : '16px',
                            borderBottomLeftRadius: m.sender === 'agent' ? '4px' : '16px',
                            background: m.sender === 'user'
                                ? 'linear-gradient(135deg, #3b82f6, #2563eb)'
                                : '#ffffff',
                            color: m.sender === 'user' ? '#ffffff' : '#1e293b',
                            fontSize: '14px', lineHeight: '1.55', wordBreak: 'break-word',
                            boxShadow: m.sender === 'user'
                                ? '0 2px 8px rgba(59,130,246,0.3)'
                                : '0 1px 4px rgba(0,0,0,0.08)',
                            border: m.sender === 'agent' ? '1px solid #e2e8f0' : 'none',
                        }}>
                            {m.text}
                        </div>
                    </div>
                ))}

                {isProcessing && (
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: '10px' }}>
                        <div style={{
                            width: '32px', height: '32px', borderRadius: '50%', flexShrink: 0,
                            background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px',
                        }}>🤖</div>
                        <div style={{
                            padding: '12px 16px', borderRadius: '16px', borderBottomLeftRadius: '4px',
                            background: '#ffffff', border: '1px solid #e2e8f0',
                            boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
                            display: 'flex', gap: '5px', alignItems: 'center',
                        }}>
                            {[0, 1, 2].map(i => (
                                <div key={i} style={{
                                    width: '7px', height: '7px', borderRadius: '50%', background: '#94a3b8',
                                    animation: `bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
                                }} />
                            ))}
                        </div>
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Input area */}
            <div style={{
                flexShrink: 0, background: '#ffffff',
                borderTop: '1px solid #e2e8f0',
                padding: voiceMode ? '20px 28px' : '16px 28px',
            }}>
                {voiceMode ? (
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px' }}>
                        {/* Orb */}
                        <div style={{
                            width: '68px', height: '68px', borderRadius: '50%',
                            background: orbColor,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '26px',
                            boxShadow: voiceState === LISTENING
                                ? `0 0 0 12px ${orbColor}22, 0 0 0 6px ${orbColor}44`
                                : `0 4px 12px ${orbColor}55`,
                            transition: 'all 0.3s',
                        }}>
                            {orbIcon}
                        </div>

                        <p style={{
                            margin: 0, fontSize: '13px', color: '#64748b',
                            minHeight: '18px', fontStyle: voiceState === LISTENING ? 'italic' : 'normal',
                        }}>
                            {statusText}
                        </p>

                        <div style={{ display: 'flex', gap: '10px' }}>
                            {voiceState === IDLE && (
                                <button onClick={startListening} style={{
                                    padding: '9px 28px',
                                    background: 'linear-gradient(135deg, #3b82f6, #2563eb)',
                                    color: '#fff', border: 'none', borderRadius: '8px',
                                    cursor: 'pointer', fontWeight: '600', fontSize: '14px',
                                    boxShadow: '0 2px 8px rgba(59,130,246,0.35)',
                                }}>
                                    ▶ Start
                                </button>
                            )}
                            {voiceState !== IDLE && (
                                <button onClick={cancelVoice} style={{
                                    padding: '9px 28px',
                                    background: '#fff', color: '#dc2626',
                                    border: '1.5px solid #fca5a5', borderRadius: '8px',
                                    cursor: 'pointer', fontWeight: '600', fontSize: '14px',
                                }}>
                                    ✕ Cancel
                                </button>
                            )}
                        </div>
                    </div>
                ) : (
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <input
                            type="text" value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && sendTextMessage()}
                            disabled={isProcessing}
                            placeholder="Message Friday..."
                            style={{
                                flex: 1, padding: '11px 16px',
                                borderRadius: '10px', border: '1.5px solid #e2e8f0',
                                fontSize: '14px', color: '#1e293b', outline: 'none',
                                background: '#f8fafc', transition: 'border 0.2s',
                            }}
                            onFocus={e => e.target.style.borderColor = '#93c5fd'}
                            onBlur={e => e.target.style.borderColor = '#e2e8f0'}
                        />
                        <button onClick={sendTextMessage} disabled={isProcessing} style={{
                            padding: '11px 22px',
                            background: isProcessing ? '#cbd5e1' : 'linear-gradient(135deg, #3b82f6, #2563eb)',
                            color: '#fff', border: 'none', borderRadius: '10px',
                            cursor: isProcessing ? 'not-allowed' : 'pointer',
                            fontWeight: '600', fontSize: '14px',
                            boxShadow: isProcessing ? 'none' : '0 2px 8px rgba(59,130,246,0.35)',
                            transition: 'all 0.2s',
                        }}>
                            Send
                        </button>
                    </div>
                )}
            </div>

            <style>{`
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { margin: 0; overflow: hidden; }
                @keyframes bounce {
                    0%, 80%, 100% { transform: translateY(0); }
                    40% { transform: translateY(-6px); }
                }
                ::-webkit-scrollbar { width: 5px; }
                ::-webkit-scrollbar-track { background: transparent; }
                ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
            `}</style>
        </div>
    );
}
