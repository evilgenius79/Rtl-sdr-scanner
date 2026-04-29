import { ref, reactive, onMounted } from 'vue';
import { api } from '../api.js';

export default {
  setup() {
    const state = reactive({
      q: '',
      category: '',
      encrypted: '',
      calls: [],
      offset: 0,
      limit: 50,
      loading: false,
    });

    async function load(reset = false) {
      if (reset) state.offset = 0;
      state.loading = true;
      try {
        const d = await api('/api/calls', {
          params: {
            q: state.q,
            category: state.category,
            encrypted: state.encrypted === '' ? undefined : state.encrypted,
            limit: state.limit,
            offset: state.offset,
          },
        });
        state.calls = reset ? d.calls : [...state.calls, ...d.calls];
      } catch {} finally {
        state.loading = false;
      }
    }

    function more() { state.offset += state.limit; load(false); }

    function fmt(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleString();
    }

    onMounted(() => load(true));

    return { state, load, more, fmt };
  },
  template: `
    <div>
      <div class="search-bar">
        <input v-model="state.q" placeholder="Search alpha tag, description, transcript…"
               @keydown.enter="load(true)" />
        <select v-model="state.encrypted" @change="load(true)">
          <option value="">All</option>
          <option value="false">Clear only</option>
          <option value="true">Encrypted only</option>
        </select>
        <button class="play" @click="load(true)">Search</button>
      </div>

      <table class="calls-table">
        <thead>
          <tr><th>Time</th><th>System</th><th>Talkgroup</th><th>Description</th><th>Duration</th><th>Audio</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in state.calls" :key="c.id" :class="{ encrypted: c.encrypted }">
            <td>{{ fmt(c.start_time) }}</td>
            <td>{{ c.system }}</td>
            <td>
              <strong>{{ c.alpha || ('TG ' + c.tgid) }}</strong>
              <span class="muted" style="font-size:0.8rem"> · {{ c.tgid }}</span>
              <span v-if="c.encrypted" class="badge danger" style="margin-left:0.4rem">ENC</span>
            </td>
            <td>{{ c.description }}</td>
            <td>{{ c.duration_seconds ? c.duration_seconds.toFixed(1) + 's' : '' }}</td>
            <td>
              <audio v-if="c.audio_url && !c.encrypted" :src="c.audio_url" controls preload="none"></audio>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:1rem; text-align:center">
        <button class="play" v-if="state.calls.length && state.calls.length % state.limit === 0" @click="more">
          {{ state.loading ? 'Loading…' : 'Load more' }}
        </button>
      </div>
    </div>
  `,
};
