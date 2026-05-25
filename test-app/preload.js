const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('netshape', {
  onMeasurement: (callback) => {
    ipcRenderer.on('measurement', (event, data) => callback(data));
  },
  getConfig: () => ipcRenderer.invoke('get-config'),
  setConfig: (payload) => ipcRenderer.invoke('set-config', payload),
});
