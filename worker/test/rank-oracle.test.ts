import assert from "node:assert/strict";
import { test } from "node:test";
import { type Claim, survivor } from "../src/memory.ts";

let n = 0;
function claim(over: Partial<Claim>): Claim {
  n++;
  return {
    id: n.toString(16).padStart(8, "0"), type: "fact", subject: "s", content: "c", scope: "project",
    project: "p", agent: null, user: null, status: "candidate", authority: "agent", confidence: 0.5,
    source_ids: [], code_refs: [], created_at: "2020-01-01", updated_at: "2020-01-01T00:00:00", ...over,
  };
}

const auth = { agent: 0, verified: 1, human: 2 } as const;

function oracle(members: Claim[]): Claim {
  return members.reduce((best, item) => {
    const key = (c: Claim) => [
      auth[c.authority], c.status === "accepted" ? 1 : 0, c.confidence ?? 0, c.source_ids.length, c.updated_at,
    ] as const;
    const a = key(item), b = key(best);
    for (let i = 0; i < a.length; i++) {
      if (a[i] > b[i]) return item;
      if (a[i] < b[i]) return best;
    }
    return best;
  });
}

function earlyReturn(members: Claim[]): Claim {
  return members.reduce((best, item) => ((item.confidence ?? 0) > (best.confidence ?? 0) ? item : best));
}

test("authority beats confidence, status, evidence, and recency in either order", () => {
  const human = claim({ authority: "human", confidence: 0.1, status: "candidate", source_ids: [], updated_at: "2019-01-01" });
  const agent = claim({
    authority: "agent", confidence: 0.99, status: "accepted",
    source_ids: ["s1", "s2", "s3"], updated_at: "2026-01-01",
  });
  for (const members of [[agent, human], [human, agent]]) {
    assert.equal(survivor(members).id, oracle(members).id);
    assert.equal(oracle(members).id, human.id);
  }
  assert.notEqual(earlyReturn([agent, human]).id, oracle([agent, human]).id);
});

test("accepted beats a higher-confidence candidate of the same authority", () => {
  const weak = claim({ authority: "verified", status: "candidate", confidence: 0.9, updated_at: "2026" });
  const strong = claim({ authority: "verified", status: "accepted", confidence: 0.2, source_ids: ["only"], updated_at: "2020" });
  for (const members of [[weak, strong], [strong, weak]]) {
    assert.equal(survivor(members).id, strong.id);
    assert.equal(survivor(members).id, oracle(members).id);
  }
});

test("every strict authority pair beats each secondary key, in either order", () => {
  const pairs = [["human", "verified"], ["human", "agent"], ["verified", "agent"]] as const;
  const edges: Partial<Claim>[] = [
    { confidence: 0.99 },
    { status: "accepted" },
    { source_ids: ["s1", "s2", "s3"] },
    { updated_at: "2026-01-01T00:00:00" },
  ];
  for (const [higher, lower] of pairs) {
    for (const edge of edges) {
      const low = claim({
        authority: lower, confidence: 0.1, status: "candidate", source_ids: [], updated_at: "2019-01-01T00:00:00",
        ...edge,
      });
      const high = claim({
        authority: higher, confidence: 0.1, status: "candidate", source_ids: [], updated_at: "2019-01-01T00:00:00",
      });
      for (const members of [[low, high], [high, low]]) {
        assert.equal(survivor(members).id, high.id);
        assert.equal(oracle(members).id, high.id);
      }
      if (edge.confidence != null) assert.notEqual(earlyReturn([low, high]).id, oracle([low, high]).id);
    }
  }
});

test("equal keys are a tie", () => {
  const left = claim({ id: "left" });
  const right = claim({ id: "right" });
  assert.ok([left.id, right.id].includes(survivor([left, right]).id));
  assert.ok([left.id, right.id].includes(survivor([right, left]).id));
});
