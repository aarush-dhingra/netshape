const express = require('express');

function createLocalServer() {
  const app = express();

  app.use(express.raw({ type: '*/*', limit: '10mb' }));

  // ── Echo endpoint ───────────────────────────────────────────────
  app.get('/api/echo', (req, res) => {
    res.json({ message: 'hello', ts: Date.now() });
  });

  // ── Slow endpoint ───────────────────────────────────────────────
  app.get('/api/slow', (req, res) => {
    const delay = parseInt(req.query.delay, 10) || 1000;
    setTimeout(() => {
      res.json({ message: 'slow response', delay });
    }, delay);
  });

  // ── Upload endpoint ─────────────────────────────────────────────
  app.post('/api/upload', (req, res) => {
    const receivedBytes = Buffer.isBuffer(req.body) ? req.body.length : 0;
    res.json({ received_bytes: receivedBytes });
  });

  // ── Stream endpoint (SSE) ───────────────────────────────────────
  app.get('/api/stream', (req, res) => {
    const n = parseInt(req.query.n, 10) || 5;
    let sent = 0;

    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    const interval = setInterval(() => {
      sent++;
      res.write(`data: {"event":${sent},"timestamp":${Date.now()}}\n\n`);
      if (sent >= n) {
        clearInterval(interval);
        res.end();
      }
    }, 500);

    req.on('close', () => {
      clearInterval(interval);
    });
  });

  // Return the *unstarted* express app so tests can mount/mixin if needed.
  return app;
}

module.exports = { createLocalServer };
