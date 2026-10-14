# Replay: one query set against every kypp store

Seeds the same 20k claims into each store and replays the same queries, so ranking parity is
measured instead of assumed. Run from `worker/` after `npm ci`; outputs go wherever you point them.

```sh
python3 -I bench/replay/gen_corpus.py ../ --out /tmp/replay              # corpus.jsonl + queries.json
npx wrangler d1 migrations apply kypp-replay --local -c bench/replay/wrangler.replay.jsonc --persist-to /tmp/replay/state
npx wrangler dev -c bench/replay/wrangler.replay.jsonc --persist-to /tmp/replay/state &   # Worker on workerd
python3 -I bench/replay/drive.py http://127.0.0.1:8787 /tmp/replay/corpus.jsonl /tmp/replay/queries.json /tmp/replay/workerd.json --seed
node --experimental-strip-types bench/replay/node_replay.ts /tmp/replay/corpus.jsonl /tmp/replay/queries.json /tmp/replay/node.json
(cd .. && uv run python -I worker/bench/replay/py_replay.py turso /tmp/replay/t.db /tmp/replay/corpus.jsonl /tmp/replay/queries.json /tmp/replay/py-turso.json)
python3 -I bench/replay/compare.py /tmp/replay/workerd.json /tmp/replay/node.json /tmp/replay/py-turso.json
```

For hosted D1, add `"remote": true` and a real `database_id` to the binding in
`wrangler.replay.jsonc` (a scratch database, never the production one) and rerun `drive.py`.
`py_replay.py sqlite|libsql` replays PR #33's adapters from a checkout of that branch.
