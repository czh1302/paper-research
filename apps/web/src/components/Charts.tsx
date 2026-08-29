import { BarChart, GraphChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react/lib/core";
import type { VisualizationData } from "../lib/types";
import { type Theme, useTheme } from "../lib/theme";

echarts.use([BarChart, GraphChart, LineChart, ScatterChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

const palettes = {
  light: { text: "#66758A", faint: "#8090A1", line: "#DCE5EC", surface: "#FFFFFF", accent: "#0F8F80", warning: "#B4650F", danger: "#B91C1C", graph: "#6F879B" },
  dark: { text: "#A9B8C5", faint: "#7D93A5", line: "#354B61", surface: "#1D2C3F", accent: "#49C5B3", warning: "#F4B860", danger: "#F87171", graph: "#7590A8" },
};

function useChartPalette() {
  const { theme } = useTheme();
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const before = () => setPrinting(true);
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => { window.removeEventListener("beforeprint", before); window.removeEventListener("afterprint", after); };
  }, []);
  const activeTheme: Theme = printing ? "light" : theme;
  return { palette: palettes[activeTheme], activeTheme };
}

function axis(palette: typeof palettes.light) {
  return {
    axisLine: { lineStyle: { color: palette.line } },
    axisTick: { lineStyle: { color: palette.line } },
    axisLabel: { color: palette.text },
    splitLine: { lineStyle: { color: palette.line, opacity: .72 } },
  };
}

function tooltip(palette: typeof palettes.light) {
  return { backgroundColor: palette.surface, borderColor: palette.line, textStyle: { color: palette.text } };
}

export function TimelineChart({ data }: { data: VisualizationData["timeline"] }) {
  const { palette, activeTheme } = useChartPalette();
  return <ReactECharts key={activeTheme} echarts={echarts} notMerge style={{ height: 280 }} option={{ tooltip: tooltip(palette), grid: { left: 45, right: 20, top: 25, bottom: 35 }, xAxis: { ...axis(palette), type: "category", data: data.map((d) => d.year) }, yAxis: { ...axis(palette), type: "value", minInterval: 1 }, series: [{ type: "line", smooth: true, data: data.map((d) => d.count), lineStyle: { color: palette.accent, width: 3 }, itemStyle: { color: palette.accent }, areaStyle: { color: palette.accent, opacity: .1 } }] }} />;
}

export function SourceChart({ data }: { data: VisualizationData["sources"] }) {
  const { palette, activeTheme } = useChartPalette();
  return <ReactECharts key={activeTheme} echarts={echarts} notMerge style={{ height: 280 }} option={{ tooltip: tooltip(palette), grid: { left: 100, right: 20, top: 15, bottom: 25 }, xAxis: { ...axis(palette), type: "value", minInterval: 1 }, yAxis: { ...axis(palette), type: "category", data: data.map((d) => d.source) }, series: [{ type: "bar", data: data.map((d) => d.count), itemStyle: { color: palette.warning, borderRadius: [0, 5, 5, 0] } }] }} />;
}

export function OpportunityChart({ data }: { data: VisualizationData["opportunities"] }) {
  const { palette, activeTheme } = useChartPalette();
  return <ReactECharts key={activeTheme} echarts={echarts} notMerge style={{ height: 340 }} option={{ tooltip: { ...tooltip(palette), formatter: (item: {data: {value: number[]; name: string}}) => `${item.data.name}<br>Feasibility: ${item.data.value[0]}<br>Impact: ${item.data.value[1]}<br>Uncertainty: ${item.data.value[2]}` }, grid: { left: 45, right: 25, top: 30, bottom: 45 }, xAxis: { ...axis(palette), name: "Feasibility", nameTextStyle: { color: palette.text }, min: 0, max: 1 }, yAxis: { ...axis(palette), name: "Impact", nameTextStyle: { color: palette.text }, min: 0, max: 1 }, visualMap: { min: 0, max: 1, dimension: 2, orient: "horizontal", left: "center", bottom: 0, textStyle: { color: palette.text }, inRange: { color: [palette.accent, palette.warning, palette.danger] } }, series: [{ type: "scatter", symbolSize: 18, data: data.map((d) => ({ name: d.name_zh, value: [d.feasibility, d.impact, d.uncertainty] })) }] }} />;
}

export function CitationGraph({ data }: { data: VisualizationData["graph"] }) {
  const { palette, activeTheme } = useChartPalette();
  const linkedIds = new Set(data.links.flatMap((link) => [link.source, link.target]));
  const nodes = data.nodes.filter((node) => linkedIds.has(node.id)).map((node) => ({ ...node, symbolSize: 10, value: node.year }));
  if (!data.links.length) return <div className="flex h-[300px] items-center justify-center px-8 text-center text-sm text-muted">当前候选集合中没有可解析的直接引用边；节点和边只来自学术 API，不使用推测关系。</div>;
  return <ReactECharts key={activeTheme} echarts={echarts} notMerge style={{ height: 360 }} option={{ tooltip: tooltip(palette), series: [{ type: "graph", layout: "force", roam: true, data: nodes, links: data.links, label: { show: false, color: palette.text }, lineStyle: { color: palette.graph, opacity: .6, curveness: .12 }, itemStyle: { color: palette.accent }, force: { repulsion: 100, edgeLength: [50, 130] }, emphasis: { label: { show: true, formatter: "{b}" }, focus: "adjacency" } }] }} />;
}
