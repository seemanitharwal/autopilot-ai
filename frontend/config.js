'use strict';

// Override before this file loads:  window.AUTOPILOT_API_BASE = 'https://your-backend.run.app';
const CONFIG = Object.freeze({
  API_BASE:       window.AUTOPILOT_API_BASE || 'http://localhost:8080',
  MAX_STEPS:      8,
  RUN_TIMEOUT_MS: 15000,
  MAX_POLL_ERRORS: 5,
});