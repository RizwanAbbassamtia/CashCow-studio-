/** The "12 of 30 tracks" line under a filtered table. */
export function CountLine({ shown, total, noun, nounPlural }: { shown: number; total: number; noun: string; nounPlural?: string }) {
  const many = nounPlural ?? `${noun}s`;
  const text = shown === total ? `${total} ${total === 1 ? noun : many}` : `${shown} of ${total} ${total === 1 ? noun : many}`;
  return <p className="border-t border-line px-5 py-2.5 text-xs text-ink-faint">{text}</p>;
}
