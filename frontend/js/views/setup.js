import { api } from '../api.js';
import { getSettings, saveSettings } from '../store.js';
import { toast } from '../ui.js';

const BASE58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

let els = {};

export function initSetup() {
  els = {
    api: document.getElementById('cfgApi'),
    token: document.getElementById('cfgToken'),
    wallet: document.getElementById('cfgWallet'),
    status: document.getElementById('setupStatus'),
  };

  const settings = getSettings();
  els.api.value = settings.apiUrl;
  els.token.value = settings.token;
  els.wallet.value = settings.wallet;

  document.getElementById('bSave').addEventListener('click', () => {
    const address = els.wallet.value.trim();
    if (address && !BASE58.test(address)) {
      return toast('Not a valid Solana address', true);
    }

    saveSettings({
      apiUrl: els.api.value.trim(),
      token: els.token.value.trim(),
      wallet: address,
    });
    toast('Saved');
  });

  document.getElementById('bTest').addEventListener('click', async () => {
    // Save first, otherwise the test checks the previous configuration.
    saveSettings({
      apiUrl: els.api.value.trim(),
      token: els.token.value.trim(),
    });

    try {
      const { data, stale } = await api.health();
      if (stale) {
        els.status.textContent = 'No connection — cached response only.';
        return toast('Backend unreachable', true);
      }
      const notes = [
        `Backend reachable. Helius key ${data.helius_configured ? 'set' : 'missing'}.`,
      ];
      if (!data.helius_configured) {
        notes.push('Without a Helius key the coin list works, but wallet sync does not.');
      }
      els.status.textContent = notes.join(' ');
      toast('Connection works');
    } catch (error) {
      els.status.textContent = error.message || 'Connection failed.';
      toast('Connection failed', true);
    }
  });

  return {};
}
