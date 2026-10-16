/** Request ownership only; no budget storage, calculation or mutation. */
export function createAuditReadGuard(revisionId: string, againstId: string) {
  let pair = JSON.stringify([revisionId, againstId]), generation = 0;
  const active = new Map<string, AbortController>();
  const cancel = (channel: string) => { active.get(channel)?.abort(); active.delete(channel); };
  const dispose = () => { for (const channel of active.keys()) cancel(channel); };
  return {
    get generation() { return generation; },
    matches(revision: string, against: string) { return pair === JSON.stringify([revision, against]); },
    setPair(revision: string, against: string) { dispose(); pair = JSON.stringify([revision, against]); return ++generation; },
    cancel,
    dispose,
    start(channel: string) {
      cancel(channel);
      const controller = new AbortController(), expectedGeneration = generation, expectedPair = pair;
      active.set(channel, controller);
      return { signal: controller.signal, current: () => !controller.signal.aborted && generation === expectedGeneration
        && pair === expectedPair && active.get(channel) === controller };
    },
  };
}
