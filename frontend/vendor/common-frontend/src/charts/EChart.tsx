import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { BarChart, HeatmapChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, HeatmapChart, LineChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

export interface EChartProps {
  option: EChartsCoreOption
  tall?: boolean
}

// option은 호출하는 쪽에서 useMemo로 고정해서 넘긴다. (원본: DejaFix components/EChart.tsx)
export function EChart({ option, tall = false }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const chart = echarts.init(el)
    chart.setOption(option)
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.dispose()
    }
  }, [option])
  return <div ref={ref} className={`chart ${tall ? 'tall' : ''}`} />
}
