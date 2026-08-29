import { BarChart, GraphChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react/lib/core";
import type { VisualizationData } from "../lib/types";

echarts.use([
  BarChart,
  GraphChart,
  LineChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

const text = { color: "#91a5ba" };
const axis = { axisLine: { lineStyle: { color: "#334155" } }, axisLabel: text, splitLine: { lineStyle: { color: "rgba(125,161,194,.12)" } } };

export function TimelineChart({ data }: { data: VisualizationData["timeline"] }) {
  return <ReactECharts echarts={echarts} style={{ height: 280 }} option={{ tooltip: {}, grid: { left: 45, right: 20, top: 25, bottom: 35 }, xAxis: { ...axis, type: "category", data: data.map((d) => d.year) }, yAxis: { ...axis, type: "value", minInterval: 1 }, series: [{ type: "line", smooth: true, data: data.map((d) => d.count), lineStyle: { color: "#36d5d2" }, itemStyle: { color: "#36d5d2" }, areaStyle: { color: "rgba(54,213,210,.12)" } }] }} />;
}

export function SourceChart({ data }: { data: VisualizationData["sources"] }) {
  return <ReactECharts echarts={echarts} style={{ height: 280 }} option={{ tooltip: {}, grid: { left: 100, right: 20, top: 15, bottom: 25 }, xAxis: { ...axis, type: "value", minInterval: 1 }, yAxis: { ...axis, type: "category", data: data.map((d) => d.source) }, series: [{ type: "bar", data: data.map((d) => d.count), itemStyle: { color: "#f5aa3c", borderRadius: [0, 5, 5, 0] } }] }} />;
}

export function OpportunityChart({ data }: { data: VisualizationData["opportunities"] }) {
  return <ReactECharts echarts={echarts} style={{ height: 340 }} option={{ tooltip: { formatter: (item: {data: {value: number[]; name: string}}) => `${item.data.name}<br>Feasibility: ${item.data.value[0]}<br>Impact: ${item.data.value[1]}<br>Uncertainty: ${item.data.value[2]}` }, grid: { left: 45, right: 25, top: 30, bottom: 45 }, xAxis: { ...axis, name: "Feasibility", nameTextStyle: text, min: 0, max: 1 }, yAxis: { ...axis, name: "Impact", nameTextStyle: text, min: 0, max: 1 }, visualMap: { min: 0, max: 1, dimension: 2, orient: "horizontal", left: "center", bottom: 0, textStyle: text, inRange: { color: ["#36d5d2", "#f5aa3c", "#ef6a78"] } }, series: [{ type: "scatter", symbolSize: 18, data: data.map((d) => ({ name: d.name_zh, value: [d.feasibility, d.impact, d.uncertainty] })) }] }} />;
}

export function CitationGraph({ data }: { data: VisualizationData["graph"] }) {
  const linkedIds = new Set(data.links.flatMap((link) => [link.source, link.target]));
  const nodes = data.nodes.filter((node) => linkedIds.has(node.id)).map((node) => ({
    ...node,
    symbolSize: 10,
    value: node.year,
  }));
  if (!data.links.length) {
    return <div className="flex h-[300px] items-center justify-center px-8 text-center text-sm text-slate-500">当前候选集合中没有可解析的直接引用边；节点和边只来自学术 API，不使用推测关系。</div>;
  }
  return <ReactECharts echarts={echarts} style={{ height: 360 }} option={{ tooltip: {}, series: [{ type: "graph", layout: "force", roam: true, data: nodes, links: data.links, label: { show: false, color: "#dce7ef" }, lineStyle: { color: "#5f7690", opacity: 0.6, curveness: 0.12 }, itemStyle: { color: "#36d5d2" }, force: { repulsion: 100, edgeLength: [50, 130] }, emphasis: { label: { show: true, formatter: "{b}" }, focus: "adjacency" } }] }} />;
}
