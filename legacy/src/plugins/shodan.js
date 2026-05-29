// Shodan plugin — parses search result HTML from the authenticated browser session.
// No API key needed. Rides the session, extracts structured host records from the page.

function parseHtmlResults(body, query) {
  const records = [];

  // Pull each result block
  const blocks = body.split('class="result"').slice(1);
  for (const block of blocks) {
    const end = block.indexOf('class="result"');
    const chunk = end > 0 ? block.slice(0, end) : block;

    const ip        = chunk.match(/href="\/host\/([\d.]+)"/)?.[1]              ?? null;
    if (!ip) continue;

    const org       = chunk.match(/class="filter-link filter-org">([^<]+)</)?.[1]?.trim()  ?? null;
    const timestamp = chunk.match(/class="timestamp[^"]*">([^<]+)</)?.[1]?.trim()          ?? null;

    // Country + city: two adjacent filter-link text-dark anchors
    const geo       = [...chunk.matchAll(/class="filter-link text-dark">([^<]+)</g)];
    const country   = geo[0]?.[1]?.trim() ?? null;
    const city      = geo[1]?.[1]?.trim() ?? null;

    const tags      = [...chunk.matchAll(/class="tag">([^<]+)</g)].map(m => m[1].trim());

    // Banner preview — content of the <pre> tag
    const banner    = chunk.match(/<pre>([\s\S]{0,300})<\/pre>/)?.[1]
                        ?.replace(/<[^>]+>/g, '').trim() ?? null;

    records.push({
      domain:      'shodan.io',
      type:        'shodan-host',
      url:         `https://www.shodan.io/host/${ip}`,
      query,
      ip,
      org,
      country,
      city,
      tags:        tags.join(',') || null,
      last_update: timestamp,
      banner,
      data:        JSON.stringify({ query, ip, org, country, city, tags, timestamp, banner }),
    });
  }

  return records;
}

function parseTotalResults(body) {
  const m = body.match(/class="total-results">([\d,]+)</);
  return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
}

function parseFacets(body) {
  const facets = {};
  const sections = body.split('<h6>').slice(1);
  for (const s of sections) {
    const label = s.match(/^([^<]+)</)?.[1]?.trim();
    if (!label) continue;
    const items = [...s.matchAll(/class="filter-link[^"]*">([^<]+)<\/a><span>([\d,]+)<\/span>/g)]
      .map(m => ({ value: m[1].trim(), count: parseInt(m[2].replace(/,/g, ''), 10) }));
    if (items.length) facets[label] = items;
  }
  return facets;
}

export default {
  name: 'shodan',

  match: (url) => /shodan\.io/i.test(url),

  async onResponse(url, body) {
    // HTML search results page
    if (/shodan\.io\/search\?/.test(url) && body.includes('class="result"')) {
      let query = 'unknown';
      try { query = decodeURIComponent(new URL(url).searchParams.get('query') ?? 'unknown'); } catch {}

      const hosts   = parseHtmlResults(body, query);
      const total   = parseTotalResults(body);
      const facets  = parseFacets(body);

      if (hosts.length > 0) {
        console.log(`[shodan] query="${query}" → ${hosts.length} hosts on page (total: ${total ?? '?'})`);
        if (Object.keys(facets).length) {
          for (const [label, items] of Object.entries(facets)) {
            console.log(`  ${label}: ${items.slice(0,3).map(i => `${i.value} (${i.count})`).join(', ')}`);
          }
        }
        return hosts;
      }
    }

    // Passive: JSON API responses if they flow through naturally (rare without key)
    if (/api\.shodan\.io/.test(url)) {
      let parsed;
      try { parsed = JSON.parse(body); } catch { return null; }
      const matches = parsed?.matches;
      if (!Array.isArray(matches)) return null;
      let q = 'unknown';
      try { q = decodeURIComponent(new URL(url).searchParams.get('query') ?? 'unknown'); } catch {}
      return matches.map(h => ({
        domain:      'shodan.io',
        type:        'shodan-host',
        url:         `https://www.shodan.io/host/${h.ip_str}`,
        query:       q,
        ip:          h.ip_str,
        org:         h.org    ?? null,
        country:     h.location?.country_name ?? null,
        city:        h.location?.city         ?? null,
        hostnames:   h.hostnames?.join(',')   ?? null,
        ports:       h.ports?.join(',')        ?? null,
        vulns:       h.vulns ? Object.keys(h.vulns).join(',') : null,
        tags:        h.tags?.join(',')         ?? null,
        last_update: h.last_update             ?? null,
        data:        JSON.stringify(h),
      }));
    }

    return null;
  },
};
