import { ref, computed, watch, onMounted } from 'vue';
import { api } from '../api.js';

export default {
  props: ['appState'],
  setup(props) {
    const recent = ref([]);
    const audio = ref(null);
    const autoplay = ref(true);

    const active = computed(() => {
      const calls = props.appState.activeCalls || [];
      // Show the most recent / loudest call.
      return calls.length ? calls[0] : null;
    });

    async function refresh() {
      try {
        const d = await api('/api/calls', { params: { limit: 25 } });
        recent.value = d.calls || [];
      } catch {}
    }

    onMounted(refresh);

    watch(() => props.appState.lastEvent, (ev) => {
      if (!ev) return;
      if (ev.type === 'call_recorded' && ev.call) {
        recent.value = [ev.call, ...recent.value].slice(0, 50);
        if (autoplay.value && !ev.call.encrypted && ev.call.audio_url && audio.value) {
          audio.value.src = ev.call.audio_url;
          audio.value.play().catch(() => { /* autoplay may be blocked */ });
        }
      }
    });

    function playUrl(url) {
      if (audio.value) {
        audio.value.src = url;
        audio.value.play().catch(() => {});
      }
    }

    function fmtTime(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleTimeString();
    }

    function fmtDur(s) {
      if (!s) return '';
      return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.floor(s % 60)}s`;
    }

    return { recent, active, audio, autoplay, playUrl, fmtTime, fmtDur };
  },
  template: `
    <div class="live-grid">
      <div class="live-card active-call" :class="{ idle: !active }">
        <h2>Active call</h2>
        <div v-if="active">
          <div class="alpha">{{ active.alpha || ('TG ' + active.tgid) }}</div>
          <div class="desc">{{ active.description || '' }}</div>
          <div class="meta">
            <span>System: {{ active.system }}</span>
            <span>TGID: {{ active.tgid }}</span>
            <span v-if="active.frequency_hz">Freq: {{ (active.frequency_hz / 1e6).toFixed(4) }} MHz</span>
            <span v-if="active.encrypted" class="badge danger">ENC</span>
          </div>
        </div>
        <div v-else class="alpha">Idle</div>
      </div>

      <div class="live-card">
        <h2>
          Audio
          <label style="float:right; font-size:0.85rem">
            <input type="checkbox" v-model="autoplay" /> auto-play
          </label>
        </h2>
        <audio ref="audio" controls></audio>
      </div>

      <div class="live-card" style="grid-column: 1 / -1">
        <h2>Recent calls</h2>
        <div class="recent-list">
          <div v-for="c in recent" :key="c.id"
               class="recent-row" :class="{ encrypted: c.encrypted }">
            <span class="time">{{ fmtTime(c.start_time) }}</span>
            <span>
              <strong>{{ c.alpha || ('TG ' + c.tgid) }}</strong>
              <span class="muted" style="margin-left:0.4rem">{{ c.category || c.system }}</span>
              <span v-if="c.encrypted" class="badge danger" style="margin-left:0.4rem">ENC</span>
              <span class="muted" style="margin-left:0.4rem; font-size:0.8rem">{{ fmtDur(c.duration_seconds) }}</span>
            </span>
            <button v-if="c.audio_url && !c.encrypted" class="play" @click="playUrl(c.audio_url)">▶</button>
          </div>
          <p v-if="!recent.length" class="muted">No calls yet. Make sure trunk-recorder is running.</p>
        </div>
      </div>
    </div>
  `,
};
