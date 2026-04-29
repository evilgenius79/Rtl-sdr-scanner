import { ref, onMounted, onUnmounted } from 'vue';
import { api } from '../api.js';

export default {
  setup() {
    const status = ref(null);
    let timer = null;

    async function refresh() {
      try { status.value = await api('/api/status'); } catch {}
    }

    onMounted(() => {
      refresh();
      timer = setInterval(refresh, 5000);
    });

    onUnmounted(() => clearInterval(timer));

    return { status };
  },
  template: `
    <div v-if="status" class="status-grid">
      <div class="status-card">
        <span class="key">RadioReference</span>
        <span class="val">
          <span v-if="status.rr_configured" class="badge ok">configured</span>
          <span v-else class="badge warn">not configured</span>
        </span>
      </div>
      <div class="status-card">
        <span class="key">trunk-recorder service</span>
        <span class="val">
          <span class="badge" :class="status.tr_service_active === 'active' ? 'ok' : 'danger'">
            {{ status.tr_service_active || 'unknown' }}
          </span>
        </span>
      </div>
      <div class="status-card">
        <span class="key">Config file</span>
        <span class="val">
          <span class="badge" :class="status.tr_config_exists ? 'ok' : 'warn'">
            {{ status.tr_config_exists ? 'present' : 'missing — run Setup' }}
          </span>
        </span>
      </div>
      <div class="status-card">
        <span class="key">SDRs detected</span>
        <span class="val">{{ status.sdrs.length }}</span>
        <ul style="margin:0.4rem 0 0 1rem; font-size:0.85rem; color:var(--muted)">
          <li v-for="s in status.sdrs" :key="s.index">
            #{{ s.index }} {{ s.name }} <span v-if="s.serial">[{{ s.serial }}]</span>
          </li>
        </ul>
      </div>
      <div class="status-card">
        <span class="key">Recordings disk free</span>
        <span class="val">{{ status.disk_free_gb }} GB</span>
      </div>
    </div>
    <p v-else class="muted">Loading…</p>
  `,
};
