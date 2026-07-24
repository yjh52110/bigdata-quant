import React from 'react';

export type Column<T> = {
  key: string;
  header: string;
  render: (row: T) => React.ReactNode;
  /** Extra classes for the desktop <td>. */
  cellClass?: string;
  /** Right-align in the desktop table (e.g. action columns). */
  alignRight?: boolean;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[];
  empty: React.ReactNode;
  loading?: React.ReactNode;
  rowKey?: (row: T, i: number) => React.Key;
};

/**
 * Renders a real table on >=sm and stacked label/value cards below that.
 *
 * The previous approach — a fixed min-width table inside overflow-x-auto —
 * kept the layout from breaking but pushed real controls off-screen on a
 * phone (the MCP enable/disable button sat at x=512 on a 375px viewport, so
 * it could not be tapped at all). Stacking avoids horizontal scrolling
 * entirely, which is what makes every column reachable on mobile.
 */
export default function ResponsiveTable<T>({ columns, rows, empty, loading, rowKey }: Props<T>) {
  if (loading) return <div className="py-6 text-center text-slate-500">{loading}</div>;
  if (rows.length === 0) return <div className="py-6 text-center text-slate-500">{empty}</div>;

  return (
    <>
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              {columns.map(c => (
                <th
                  key={c.key}
                  className={`py-3 px-4 text-sm font-semibold text-slate-400 ${c.alignRight ? 'text-right' : ''}`}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={rowKey ? rowKey(row, i) : i} className="border-b border-slate-700/30 hover:bg-slate-800/40 transition-colors">
                {columns.map(c => (
                  <td key={c.key} className={`py-3 px-4 ${c.alignRight ? 'text-right' : ''} ${c.cellClass || ''}`}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="sm:hidden space-y-3">
        {rows.map((row, i) => (
          <div key={rowKey ? rowKey(row, i) : i} className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3 space-y-2">
            {columns.map(c => (
              <div key={c.key} className="flex items-center justify-between gap-3">
                <span className="text-xs text-slate-500 flex-shrink-0">{c.header}</span>
                <span className={`text-sm text-right min-w-0 break-words ${c.cellClass || 'text-slate-200'}`}>
                  {c.render(row)}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
