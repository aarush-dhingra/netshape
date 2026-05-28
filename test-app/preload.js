const { contextBridge, ipcRenderer } = require('electron');

// Tell the main process to push streaming events (llm-token etc.) to this window.
ipcRenderer.send('register-listener');

contextBridge.exposeInMainWorld('netshape', {
  // Register to receive live streaming test results (llm-token, etc.)
  onResult: (callback) => {
    const handler = (event, data) => callback(data);
    ipcRenderer.on('test-result', handler);
  },

  // One-shot test invocations
  runTest: (testType, ...args) =>
    ipcRenderer.invoke(`test-${testType}`, ...args),

  // Get current proxy status (read-only)
  getProxyStatus: () => ipcRenderer.invoke('get-proxy-status'),

  // Get cumulative session stats (requests, bytes, avg latency)
  getSessionStats: () => ipcRenderer.invoke('get-session-stats'),

  // Unregister this window from the streaming push list
  unregister: () => ipcRenderer.send('unregister-listener'),
});
