"use client";

import { type FocusEvent, type PointerEvent, useState } from "react";

// Single-series charts for the consoles. One hue (primary, 4.2:1 on the card surface), thin marks with
// 4px rounded data ends, hairline solid gridlines, selective direct labels, a hover/focus tooltip, and a
// table view so no value depends on hovering.

type Unit = { one: string; other: string };

const count = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const unitFor = (value: number, unit: Unit) => (value === 1 ? unit.one : unit.other);

/** 0 and evenly spaced ticks up to a clean maximum (steps of 1, 2 or 5 × 10ⁿ), at most four steps. */
function niceScale(max: number): number[] {
  if (max <= 0) return [0, 1, 2, 3, 4];
  const rough = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= rough) ?? 10 * magnitude;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => Math.round(i * step * 1e6) / 1e6);
}

type Tip = { index: number; x: number; y: number };

function useTooltip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (index: number) => (event: PointerEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    const mark = event.currentTarget.querySelector<HTMLElement>("[data-mark]") ?? event.currentTarget;
    const frame = event.currentTarget.closest<HTMLElement>("[data-chart]")!.getBoundingClientRect();
    const box = mark.getBoundingClientRect();
    setTip({ index, x: box.left + box.width / 2 - frame.left, y: box.top - frame.top });
  };
  return { tip, show, hide: () => setTip(null) };
}

function Tooltip({ tip, value, label }: { tip: Tip; value: string; label: string }) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg bg-ink px-2.5 py-1.5 text-xs text-white shadow-[0_8px_24px_-8px_rgb(29_33_66/0.5)]"
      style={{ left: tip.x, top: tip.y - 8 }}
    >
      <span className="font-semibold">{value}</span> <span className="text-white/75">{label}</span>
    </div>
  );
}

function TableView({ caption, head, rows }: { caption: string; head: [string, string]; rows: [string, string][] }) {
  return (
    <details className="group mt-4 text-[13px]">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded font-semibold text-primary-strong underline-offset-2 hover:underline">
        <span className="group-open:hidden">View as table</span>
        <span className="hidden group-open:inline">Hide table</span>
      </summary>
      <table className="mt-2 w-full text-left">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-xs uppercase tracking-wider text-muted">
          <tr className="border-b border-line">
            <th scope="col" className="py-1.5 font-semibold">
              {head[0]}
            </th>
            <th scope="col" className="py-1.5 text-right font-semibold">
              {head[1]}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label} className="border-b border-line last:border-b-0">
              <th scope="row" className="py-1.5 font-normal text-ink">
                {label}
              </th>
              <td className="py-1.5 text-right tabular-nums text-ink">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** Horizontal bars for one measure across named categories; every bar carries its value at the tip. */
export function BarChart({
  caption,
  bars,
  unit,
}: {
  caption: string;
  bars: { key: string; label: string; value: number }[];
  unit: Unit;
}) {
  const { tip, show, hide } = useTooltip();
  const max = Math.max(0, ...bars.map((bar) => bar.value));
  const total = bars.reduce((sum, bar) => sum + bar.value, 0);
  const share = (value: number) => (total ? Math.round((value / total) * 100) : 0);

  return (
    <div>
      <div data-chart className="relative">
        <ul className="space-y-3" onPointerLeave={hide}>
          {bars.map((bar, index) => (
            <li
              key={bar.key}
              tabIndex={0}
              aria-label={`${bar.label}: ${bar.value} ${unitFor(bar.value, unit)}, ${share(bar.value)}%`}
              onPointerEnter={show(index)}
              onFocus={show(index)}
              onBlur={hide}
              className="group grid grid-cols-[minmax(6.5rem,9.5rem)_minmax(0,1fr)] items-center gap-3 rounded-md outline-offset-4"
            >
              <span className="truncate text-sm text-ink">{bar.label}</span>
              <span className="flex h-6 items-center gap-2 border-l border-line">
                <span
                  data-mark
                  className="h-5 shrink-0 rounded-r-[4px] bg-primary transition-colors group-hover:bg-primary-strong group-focus-visible:bg-primary-strong"
                  style={{ width: max ? `calc((100% - 3rem) * ${bar.value / max})` : 0 }}
                />
                <span className="text-[13px] font-semibold tabular-nums text-ink">{count.format(bar.value)}</span>
              </span>
            </li>
          ))}
        </ul>
        {tip && (
          <Tooltip
            tip={tip}
            value={`${bars[tip.index].value} ${unitFor(bars[tip.index].value, unit)}`}
            label={`${bars[tip.index].label} · ${share(bars[tip.index].value)}%`}
          />
        )}
      </div>
      <TableView
        caption={caption}
        head={["Category", "Count"]}
        rows={bars.map((bar) => [bar.label, `${bar.value} (${share(bar.value)}%)`])}
      />
    </div>
  );
}

/**
 * Columns for one count per period. Labels the latest period and the peak on their caps; the y axis and
 * the tooltip carry the rest. The x axis labels every third period on narrow cards and every second on medium ones.
 */
export function ColumnChart({
  caption,
  points,
  unit,
}: {
  caption: string;
  points: { key: string; label: string; title: string; value: number }[];
  unit: Unit;
}) {
  const { tip, show, hide } = useTooltip();
  const ticks = niceScale(Math.max(0, ...points.map((point) => point.value)));
  const top = ticks.at(-1)!;
  const last = points.length - 1;
  const peak = points.reduce((best, point, index) => (point.value > points[best].value ? index : best), 0);

  return (
    <div className="@container">
      <div data-chart className="relative grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
        <div className="relative h-44" aria-hidden="true">
          {ticks.map((tick) => (
            <span
              key={tick}
              className="absolute right-0 translate-y-1/2 text-[11px] tabular-nums leading-none text-muted"
              style={{ bottom: `${(tick / top) * 100}%` }}
            >
              {count.format(tick)}
            </span>
          ))}
        </div>

        <div className="relative h-44">
          {ticks.map((tick) => (
            <span
              key={tick}
              aria-hidden="true"
              className="absolute inset-x-0 h-px bg-line"
              style={{ bottom: `${(tick / top) * 100}%` }}
            />
          ))}
          <ul className="absolute inset-0 flex" onPointerLeave={hide}>
            {points.map((point, index) => {
              const labelled = point.value > 0 && (index === last || index === peak);
              return (
                <li
                  key={point.key}
                  tabIndex={0}
                  aria-label={`${point.title}: ${point.value} ${unitFor(point.value, unit)}`}
                  onPointerEnter={show(index)}
                  onFocus={show(index)}
                  onBlur={hide}
                  className="group relative flex h-full flex-1 items-end justify-center rounded-md outline-offset-2"
                >
                  <span
                    data-mark
                    className="relative w-[min(24px,62%)] rounded-t-[4px] bg-primary transition-colors group-hover:bg-primary-strong group-focus-visible:bg-primary-strong"
                    style={{ height: `${(point.value / top) * 100}%` }}
                  >
                    {labelled && (
                      <span
                        aria-hidden="true"
                        className="absolute bottom-full left-1/2 mb-1 -translate-x-1/2 text-[11px] font-semibold tabular-nums text-ink"
                      >
                        {point.value}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>

        <div />
        <div className="mt-2 flex" aria-hidden="true">
          {points.map((point, index) => {
            // Narrow: every third label, medium: every second, wide: all, always counting back from the latest.
            const every3 = (last - index) % 3 === 0;
            const every2 = (last - index) % 2 === 0;
            return (
              <span
                key={point.key}
                className={`flex-1 truncate text-center text-[11px] text-muted ${every3 ? "block" : "hidden"} ${
                  every2 ? "@md:block" : "@md:hidden"
                } @3xl:block`}
              >
                {point.label}
              </span>
            );
          })}
        </div>

        {tip && (
          <Tooltip
            tip={tip}
            value={`${points[tip.index].value} ${unitFor(points[tip.index].value, unit)}`}
            label={points[tip.index].title}
          />
        )}
      </div>
      <TableView caption={caption} head={["Period", "Count"]} rows={points.map((point) => [point.title, String(point.value)])} />
    </div>
  );
}
