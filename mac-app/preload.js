const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('studio', {
  api: (route, method, data) => ipcRenderer.invoke('api', route, method, data),
  copy: value => ipcRenderer.invoke('copy', value),
});
