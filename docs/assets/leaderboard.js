function leaderboard() {
  return {
    data: { entries: [] },
    search: '',
    tierFilter: '',

    async load() {
      try {
        const res = await fetch('data/leaderboard.json');
        if (res.ok) {
          this.data = await res.json();
        }
      } catch (e) {
        console.error('Failed to load leaderboard.json', e);
      }
    },

    get filteredEntries() {
      if (!this.data || !this.data.entries) return [];
      return this.data.entries.filter(e => {
        if (this.search && !e.display_name.toLowerCase().includes(this.search.toLowerCase())
            && !e.model.toLowerCase().includes(this.search.toLowerCase())) return false;
        if (this.tierFilter === 'official' && e.tier !== 'official') return false;
        if (this.tierFilter === 'verified' && !['official', 'verified'].includes(e.tier)) return false;
        return true;
      });
    },

    tierIcon(tier) {
      return {
        official: '🏅',
        verified: '✅',
        unverified: '⚠️',
        challenged: '🚩',
        invalidated: '❌',
      }[tier] || '?';
    },

    formatBb(value) {
      if (value === null || value === undefined) return '—';
      const sign = value >= 0 ? '+' : '';
      return sign + value.toFixed(1);
    },

    formatScore(value) {
      if (value === null || value === undefined) return '—';
      return Number(value).toFixed(1);
    },

    formatPct(value) {
      if (value === null || value === undefined) return '—';
      return (Number(value) * 100).toFixed(0) + '%';
    },
  };
}
