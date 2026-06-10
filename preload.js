const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld('api', {
    sendMessage:     (text) => ipcRenderer.send('user-message', text),
    onResponse:      (cb)   => ipcRenderer.on('agent-response', (_e, res) => cb(res)),
    transcribeAudio: (b64, mime) => ipcRenderer.invoke('transcribe-audio', b64, mime),
});
