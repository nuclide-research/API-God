// X.com — intercepts SearchTimeline GraphQL responses and extracts structured tweets.
// Ported from github.com/nuclide-research/api-god-x (api-god-x.py)

export default {
  name: 'x',
  match: (url) => url.includes('SearchTimeline') && url.includes('x.com'),

  async onResponse(url, body) {
    let data;
    try { data = JSON.parse(body); } catch { return null; }

    const tweets = extractTweets(data);
    if (!tweets.length) return null;

    return tweets.map(t => ({
      domain: 'x.com',
      type:   'x-tweet',
      url,
      data:   JSON.stringify(t),
    }));
  },
}

// ── parsers (ported 1:1 from api-god-x.py) ──────────────────────────────────

function parseTweet(itemContent) {
  try {
    let result = itemContent.tweet_results?.result;
    if (!result) return null;
    if (result.__typename === 'TweetWithVisibilityResults') result = result.tweet ?? result;

    const legacy     = result.legacy ?? {};
    const userResult = result.core?.user_results?.result ?? {};
    const user       = userResult.legacy ?? {};
    const userCore   = userResult.core ?? {};

    const tweetId    = legacy.id_str ?? result.rest_id ?? '';
    const screenName = userCore.screen_name ?? user.screen_name ?? '_';

    return {
      id:         tweetId,
      url:        `https://x.com/${screenName}/status/${tweetId}`,
      created_at: legacy.created_at ?? '',
      text:       legacy.full_text  ?? legacy.text ?? '',
      lang:       legacy.lang       ?? '',
      is_retweet: 'retweeted_status_result' in legacy,
      author: {
        name:        userCore.name        ?? user.name        ?? '',
        screen_name: screenName,
        verified:    user.verified        ?? false,
        blue:        userResult.is_blue_verified ?? false,
        followers:   user.followers_count ?? 0,
      },
      metrics: {
        replies:  legacy.reply_count    ?? 0,
        retweets: legacy.retweet_count  ?? 0,
        likes:    legacy.favorite_count ?? 0,
        quotes:   legacy.quote_count    ?? 0,
        views:    parseInt(result.views?.count ?? '0', 10),
      },
    };
  } catch {
    return null;
  }
}

function extractTweets(data) {
  const tweets = [];
  let instructions;

  try {
    instructions = data.data.search_by_raw_query.search_timeline.timeline.instructions;
  } catch {
    return tweets;
  }

  for (const instruction of instructions) {
    if (instruction.type !== 'TimelineAddEntries') continue;
    for (const entry of instruction.entries ?? []) {
      const content = entry.content ?? {};
      if (content.entryType === 'TimelineTimelineCursor') continue;

      const ic = content.itemContent ?? {};
      if (ic.itemType === 'TimelineTweet') {
        const t = parseTweet(ic);
        if (t) tweets.push(t);
      }

      for (const item of content.items ?? []) {
        const ic2 = item.item?.itemContent ?? {};
        if (ic2.itemType === 'TimelineTweet') {
          const t = parseTweet(ic2);
          if (t) tweets.push(t);
        }
      }
    }
  }

  return tweets;
}
