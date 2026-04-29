import { ref, reactive, computed } from 'vue';
import { api } from '../api.js';

export default {
  setup() {
    const state = reactive({
      zip: '',
      step: 'zip',
      preview: null,
      error: '',
      loading: false,
      selectedTrsSid: null,
      selectedSiteIndex: 0,
      selectedCategories: [],
      includeConventional: true,
      hideEncrypted: true,
      dongleSerials: ['00000101', '00000102', '00000103'],
      applied: null,
    });

    async function lookup() {
      state.error = '';
      state.loading = true;
      try {
        const d = await api('/api/setup/lookup', {
          method: 'POST',
          body: { zip: state.zip },
        });
        state.preview = d;
        state.step = 'pick';
        if (d.trs_systems.length === 1) state.selectedTrsSid = d.trs_systems[0].sid;
      } catch (e) {
        state.error = e.message;
      } finally {
        state.loading = false;
      }
    }

    const selectedTrs = computed(() => {
      if (!state.preview) return null;
      return state.preview.trs_systems.find(t => t.sid === state.selectedTrsSid) || null;
    });

    const categories = computed(() => {
      if (!selectedTrs.value) return [];
      const set = new Set(selectedTrs.value.talkgroups.map(t => t.category).filter(Boolean));
      return [...set].sort();
    });

    const selectedSite = computed(() => {
      const t = selectedTrs.value;
      if (!t || !t.sites.length) return null;
      return t.sites[Math.min(state.selectedSiteIndex, t.sites.length - 1)];
    });

    const usableHz = 1_800_000;
    const siteFits = computed(() => {
      const s = selectedSite.value;
      if (!s) return null;
      return { spanMhz: (s.span_hz / 1e6).toFixed(2), needsTwo: s.span_hz > usableHz, fits: s.span_hz <= 2 * usableHz };
    });

    async function apply() {
      state.error = '';
      state.loading = true;
      try {
        const d = await api('/api/setup/apply', {
          method: 'POST',
          body: {
            zip: state.zip,
            selected_trs_sid: state.selectedTrsSid,
            selected_site_index: state.selectedSiteIndex,
            selected_categories: state.selectedCategories,
            include_conventional: state.includeConventional,
            hide_encrypted: state.hideEncrypted,
            dongle_serials: state.dongleSerials,
          },
        });
        state.applied = d;
        state.step = 'done';
      } catch (e) {
        state.error = e.message;
      } finally {
        state.loading = false;
      }
    }

    return { state, lookup, apply, selectedTrs, selectedSite, categories, siteFits };
  },
  template: `
    <div>
      <div v-if="state.step === 'zip'" class="setup-step">
        <h2>Step 1 — Enter your ZIP code</h2>
        <p class="muted">We'll fetch the systems and frequencies that serve your area from RadioReference.com.</p>
        <input v-model="state.zip" maxlength="5" pattern="\\d{5}" placeholder="46173" />
        <button class="primary" :disabled="state.loading || !/^\\d{5}$/.test(state.zip)" @click="lookup" style="margin-left:0.5rem">
          {{ state.loading ? 'Looking up…' : 'Look up' }}
        </button>
        <p v-if="state.error" class="error">{{ state.error }}</p>
      </div>

      <div v-if="state.step === 'pick' && state.preview" class="setup-step">
        <h2>Step 2 — Pick what to monitor</h2>
        <p>Found <strong>{{ state.preview.county }}, {{ state.preview.state }}</strong>.</p>

        <div v-if="state.preview.trs_systems.length === 0">
          <p class="muted">No trunked systems found for this area.</p>
        </div>

        <div v-for="trs in state.preview.trs_systems" :key="trs.sid" style="margin-bottom:1rem">
          <label style="display:flex; gap:0.5rem; align-items:center; cursor:pointer">
            <input type="radio" :value="trs.sid" v-model="state.selectedTrsSid" />
            <strong>{{ trs.name }}</strong>
            <span class="muted">{{ trs.flavor }} · {{ trs.talkgroup_count }} talkgroups · {{ trs.encrypted_count }} encrypted</span>
          </label>
        </div>

        <div v-if="selectedTrs && selectedTrs.sites.length > 1">
          <h3>Site / simulcast cell</h3>
          <select v-model.number="state.selectedSiteIndex" style="background:var(--bg-elev); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:0.4rem">
            <option v-for="(s, i) in selectedTrs.sites" :value="i" :key="i">
              {{ s.name }} (RFSS {{ s.rfss }} site {{ s.site_number }}) — span {{ (s.span_hz / 1e6).toFixed(2) }} MHz
            </option>
          </select>
        </div>

        <div v-if="siteFits" style="margin-top:0.6rem">
          <span v-if="!siteFits.needsTwo" class="badge ok">1 SDR covers site span ({{ siteFits.spanMhz }} MHz)</span>
          <span v-else-if="siteFits.fits" class="badge warn">2 SDRs needed for {{ siteFits.spanMhz }} MHz site span</span>
          <span v-else class="badge danger">Site too wide ({{ siteFits.spanMhz }} MHz) — needs 4+ SDRs</span>
        </div>

        <h3>Categories</h3>
        <div class="cat-grid">
          <label v-for="c in categories" :key="c">
            <input type="checkbox" :value="c" v-model="state.selectedCategories" />
            <span>{{ c }}</span>
          </label>
        </div>

        <h3>Options</h3>
        <label><input type="checkbox" v-model="state.hideEncrypted" /> Hide encrypted talkgroups</label><br />
        <label><input type="checkbox" v-model="state.includeConventional" /> Include conventional VHF/UHF channels (uses SDR 3)</label>

        <h3>SDR serials</h3>
        <p class="muted" style="font-size:0.85rem">Defaults match flash-sdr-serials.sh.</p>
        <input v-model="state.dongleSerials[0]" maxlength="8" /> control_low &nbsp;
        <input v-model="state.dongleSerials[1]" maxlength="8" /> voice_high &nbsp;
        <input v-model="state.dongleSerials[2]" maxlength="8" /> conventional

        <div style="margin-top:1.5rem">
          <button class="primary" :disabled="state.loading" @click="apply">
            {{ state.loading ? 'Saving…' : 'Save & generate trunk-recorder config' }}
          </button>
        </div>
        <p v-if="state.error" class="error">{{ state.error }}</p>
      </div>

      <div v-if="state.step === 'done'" class="setup-step">
        <h2>Done</h2>
        <p>Config written to <code>{{ state.applied.config_path }}</code>.</p>
        <p class="muted">{{ state.applied.next }}</p>
        <p class="muted">Run on the Pi: <code>sudo systemctl restart trunk-recorder.service</code></p>
        <a href="#live"><button class="primary">Go to Live view</button></a>
      </div>
    </div>
  `,
};
