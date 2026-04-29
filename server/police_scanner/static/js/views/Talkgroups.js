import { ref, reactive, computed, onMounted } from '/static/js/vendor/vue.esm-browser.prod.js';
import { api } from '../api.js';

export default {
  setup() {
    const state = reactive({
      talkgroups: [],
      filter: '',
      category: '',
    });

    async function load() {
      try {
        const d = await api('/api/talkgroups');
        state.talkgroups = d.talkgroups || [];
      } catch {}
    }

    const filtered = computed(() => {
      const q = state.filter.toLowerCase();
      return state.talkgroups.filter(t => {
        if (state.category && t.category !== state.category) return false;
        if (!q) return true;
        return (t.alpha || '').toLowerCase().includes(q)
            || (t.description || '').toLowerCase().includes(q)
            || String(t.tgid).includes(q);
      });
    });

    const categories = computed(() => {
      return [...new Set(state.talkgroups.map(t => t.category).filter(Boolean))].sort();
    });

    async function update(tg, payload) {
      Object.assign(tg, payload);
      try { await api('/api/talkgroups/' + tg.id, { method: 'POST', body: payload }); } catch {}
    }

    onMounted(load);

    return { state, filtered, categories, update };
  },
  template: `
    <div>
      <div class="search-bar">
        <input v-model="state.filter" placeholder="Search…" />
        <select v-model="state.category">
          <option value="">All categories</option>
          <option v-for="c in categories" :value="c" :key="c">{{ c }}</option>
        </select>
      </div>
      <table class="tg-table">
        <thead>
          <tr><th>TGID</th><th>Alpha</th><th>Description</th><th>Category</th><th>Mode</th><th>Held</th><th>Avoided</th><th>Hidden</th></tr>
        </thead>
        <tbody>
          <tr v-for="tg in filtered" :key="tg.id" :class="{ encrypted: tg.encrypted }">
            <td>{{ tg.tgid }}</td>
            <td><strong>{{ tg.alpha }}</strong></td>
            <td>{{ tg.description }}</td>
            <td>{{ tg.category }}</td>
            <td>{{ tg.mode }}</td>
            <td><input type="checkbox" :checked="tg.held" @change="update(tg, { held: $event.target.checked })" /></td>
            <td><input type="checkbox" :checked="tg.avoided" @change="update(tg, { avoided: $event.target.checked })" /></td>
            <td><input type="checkbox" :checked="tg.hidden" @change="update(tg, { hidden: $event.target.checked })" /></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!filtered.length" class="muted" style="margin-top:1rem">No talkgroups loaded yet. Run the Setup wizard first.</p>
    </div>
  `,
};
