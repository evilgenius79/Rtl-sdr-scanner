import { createApp, reactive, ref, onMounted, onUnmounted, computed, watch, h } from '/static/js/vendor/vue.esm-browser.prod.js';
import { api, connectWS } from './api.js';
import LiveView from './views/Live.js';
import SetupView from './views/Setup.js';
import RecordingsView from './views/Recordings.js';
import TalkgroupsView from './views/Talkgroups.js';
import StatusView from './views/Status.js';

const VIEWS = {
  live: { label: 'Live', component: LiveView },
  recordings: { label: 'Recordings', component: RecordingsView },
  talkgroups: { label: 'Talkgroups', component: TalkgroupsView },
  setup: { label: 'Setup', component: SetupView },
  status: { label: 'Status', component: StatusView },
};

const App = {
  setup() {
    const state = reactive({
      view: location.hash.replace('#', '') || 'live',
      user: null,
      wsState: 'connecting',
      activeCalls: [],
      recentCalls: [],
      lastEvent: null,
    });

    let ws = null;
    let reconnectTimer = null;

    function setupWS() {
      ws = connectWS((msg) => {
        state.lastEvent = msg;
        switch (msg.type) {
          case 'calls_active':
            state.activeCalls = Array.isArray(msg.calls) ? msg.calls : [];
            break;
          case 'call_recorded':
            if (msg.call) {
              state.recentCalls = [msg.call, ...state.recentCalls].slice(0, 50);
            }
            break;
          case 'call_start':
          case 'call_end':
            // forwarded; per-view components react via lastEvent watcher
            break;
        }
      });
      ws.addEventListener('open', () => { state.wsState = 'live'; });
      ws.addEventListener('close', () => {
        state.wsState = 'dead';
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(setupWS, 3000);
      });
      ws.addEventListener('error', () => { state.wsState = 'dead'; });
    }

    function navigate(view) {
      state.view = view;
      location.hash = view;
    }

    onMounted(async () => {
      try {
        state.user = await api('/api/auth/me');
      } catch {
        location.href = '/login';
        return;
      }
      setupWS();
      window.addEventListener('hashchange', () => {
        state.view = location.hash.replace('#', '') || 'live';
      });
    });

    onUnmounted(() => {
      if (ws) ws.close();
      clearTimeout(reconnectTimer);
    });

    async function logout() {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
      location.href = '/login';
    }

    return { state, navigate, logout, VIEWS };
  },
  template: `
    <div class="app-shell">
      <header class="topbar">
        <h1>📡 Scanner</h1>
        <nav>
          <button v-for="(v, key) in VIEWS" :key="key"
                  :class="{ active: state.view === key }"
                  @click="navigate(key)">{{ v.label }}</button>
        </nav>
        <div class="grow"></div>
        <span class="muted" style="font-size:0.85rem">
          <span class="ws-dot" :class="state.wsState"></span>
          {{ state.wsState === 'live' ? 'live' : 'reconnecting…' }}
        </span>
        <span class="muted" v-if="state.user">{{ state.user.username }}</span>
        <button class="play" @click="logout">Sign out</button>
      </header>
      <main class="page">
        <component :is="VIEWS[state.view]?.component" :app-state="state" />
      </main>
    </div>
  `,
};

createApp(App).mount('#app');
