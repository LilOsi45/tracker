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
      return toast('Das ist keine gültige Solana-Adresse', true);
    }

    saveSettings({
      apiUrl: els.api.value.trim(),
      token: els.token.value.trim(),
      wallet: address,
    });
    toast('Gesichert');
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
        els.status.textContent = 'Keine Verbindung — nur zwischengespeicherte Antwort.';
        return toast('Backend nicht erreichbar', true);
      }
      const notes = [
        `Backend erreichbar. Helius-Key ${data.helius_configured ? 'gesetzt' : 'fehlt'}.`,
      ];
      if (!data.helius_configured) {
        notes.push('Ohne Helius-Key läuft die Coin-Übersicht, aber kein Wallet-Sync.');
      }
      els.status.textContent = notes.join(' ');
      toast('Verbindung steht');
    } catch (error) {
      els.status.textContent = error.message || 'Verbindung fehlgeschlagen.';
      toast('Verbindung fehlgeschlagen', true);
    }
  });

  return {};
}
