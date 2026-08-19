import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  WarningOutlined
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Empty, Progress, Row, Space, Statistic, Tag, Typography } from "antd";
import dayjs from "dayjs";

import { api, getErrorMessage } from "../api";
import PageError from "../components/PageError";
import PageTitle from "../components/PageTitle";

const percent = (value: number) => Math.round((value || 0) * 1000) / 10;
const statusColor = (status?: string) => status === "ok" ? "success" : status === "disabled" ? "default" : "warning";

export default function ObservabilityPage() {
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: api.systemHealth,
    refetchInterval: 30_000
  });
  const metrics = useQuery({
    queryKey: ["online-metrics"],
    queryFn: () => api.onlineMetrics(),
    refetchInterval: 30_000
  });

  const refresh = () => void Promise.all([health.refetch(), metrics.refetch()]);
  if (health.isError && !health.data) {
    return <PageError title="系统状态加载失败" description={getErrorMessage(health.error)} onRetry={refresh} />;
  }

  const checks = health.data?.checks || {};
  const retrieval = metrics.data?.retrieval;
  const usage = metrics.data?.model_usage;
  const eventCount = Math.max(retrieval?.event_count || 0, usage?.event_count || 0);

  return (
    <div className="observability-page">
      <PageTitle
        eyebrow="OPERATIONS"
        title="系统健康与 RAG 观测"
        description="查看知识索引、模型配置、可选 Redis、检索延迟与模型费用；刷新不会调用大模型。"
        action={<Button icon={<ReloadOutlined />} loading={health.isFetching || metrics.isFetching} onClick={refresh}>刷新状态</Button>}
      />

      <Alert
        type={health.data?.status === "ok" ? "success" : "warning"}
        showIcon
        message={health.data?.status === "ok" ? "必需服务均已就绪" : "部分组件处于降级或未配置状态"}
        description={`最近检查：${health.data?.checked_at ? dayjs(health.data.checked_at).format("YYYY-MM-DD HH:mm:ss") : "--"}。Redis 是可选组件，disabled 不影响单机运行。`}
        className="section-alert"
      />

      <Row gutter={[16, 16]}>
        {[
          ["database", "数据库", <DatabaseOutlined key="db" />],
          ["knowledge", "知识索引", <CloudServerOutlined key="kb" />],
          ["models", "模型配置", <ApiOutlined key="api" />],
          ["redis", "Redis 协调", <CloudServerOutlined key="redis" />]
        ].map(([key, label, icon]) => {
          const check = checks[String(key)];
          return (
            <Col xs={24} sm={12} xl={6} key={String(key)}>
              <Card className="health-card">
                <Space align="start">
                  <span className="health-icon">{icon}</span>
                  <div>
                    <Typography.Text type="secondary">{label}</Typography.Text>
                    <div><Tag color={statusColor(check?.status)}>{String(check?.status || "loading")}</Tag></div>
                  </div>
                </Space>
              </Card>
            </Col>
          );
        })}
      </Row>

      <Typography.Title level={4} className="section-title">线上聚合指标</Typography.Title>
      {metrics.isError ? (
        <Alert type="error" showIcon message="指标加载失败" description={getErrorMessage(metrics.error)} />
      ) : eventCount === 0 ? (
        <Card><Empty description="尚无观测事件；正常咨询产生数据后将在这里展示" /></Card>
      ) : (
        <>
          {eventCount < 30 && <Alert type="info" showIcon message={`当前仅 ${eventCount} 条事件，数据用于链路验证，不应据此调整检索阈值。`} className="section-alert" />}
          <Row gutter={[16, 16]}>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="咨询事件" value={eventCount} prefix={<CheckCircleOutlined />} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="检索 P95" value={retrieval?.latency_ms.p95 || 0} precision={1} suffix="ms" /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="累计估算费用" value={usage?.cost_cny_total || 0} precision={6} prefix="¥" /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="平均 Provider Token" value={usage?.provider_token_count.mean || 0} precision={1} /></Card></Col>
          </Row>
          <Row gutter={[16, 16]} className="metrics-detail-row">
            <Col xs={24} lg={12}>
              <Card title="检索质量信号">
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="拒答率"><Progress percent={percent(retrieval?.no_answer_rate || 0)} status="normal" /></Descriptions.Item>
                  <Descriptions.Item label="错误率"><Progress percent={percent(retrieval?.error_rate || 0)} status={(retrieval?.error_rate || 0) > 0 ? "exception" : "success"} /></Descriptions.Item>
                  <Descriptions.Item label="重排触发率"><Progress percent={percent(retrieval?.rerank_trigger_rate || 0)} /></Descriptions.Item>
                  <Descriptions.Item label="重排回退率"><Progress percent={percent(retrieval?.rerank_fallback_rate || 0)} status={(retrieval?.rerank_fallback_rate || 0) > 0 ? "exception" : "normal"} /></Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card title="调用与费用完整性">
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="失败调用率"><Progress percent={percent(usage?.failed_call_rate || 0)} status={(usage?.failed_call_rate || 0) > 0 ? "exception" : "success"} /></Descriptions.Item>
                  <Descriptions.Item label="费用覆盖率"><Progress percent={percent(usage?.cost_coverage_rate || 0)} /></Descriptions.Item>
                  <Descriptions.Item label="平均单次费用">¥ {(usage?.cost_cny_per_consultation.mean || 0).toFixed(6)}</Descriptions.Item>
                  <Descriptions.Item label="无效 JSONL 行">{(retrieval?.invalid_jsonl_lines || 0) + (usage?.invalid_jsonl_lines || 0)} {(retrieval?.invalid_jsonl_lines || 0) + (usage?.invalid_jsonl_lines || 0) > 0 && <WarningOutlined />}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
