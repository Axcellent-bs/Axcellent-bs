import type { Key, ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface Column<T> {
  key: string
  header: ReactNode
  /** 칸 내용. 없으면 row[key]를 그대로 쓴다 */
  render?: (row: T) => ReactNode
  /** 숫자 칸: 오른쪽 정렬 + 고정폭 숫자 (td.num) */
  numeric?: boolean
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => Key
  /** 강조할 행 (tr.hl) */
  highlight?: (row: T) => boolean
}

/** DejaFix 표: .table-wrap > table.data. 좁은 화면에서는 가로로 스크롤된다. */
export function DataTable<T>({ columns, rows, rowKey, highlight }: DataTableProps<T>) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={cx(c.numeric && 'num')}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className={cx(highlight?.(row) && 'hl')}>
              {columns.map((c) => (
                <td key={c.key} className={cx(c.numeric && 'num')}>
                  {c.render ? c.render(row) : ((row as Record<string, unknown>)[c.key] as ReactNode)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
