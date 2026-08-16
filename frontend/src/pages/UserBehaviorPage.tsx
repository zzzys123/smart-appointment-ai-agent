import { useState } from "react";
import { BellOutlined, CalendarOutlined, ClockCircleOutlined, HeartOutlined, ReloadOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Descriptions, Empty, Modal, Row, Skeleton, Space, Statistic, Tag, Typography, message } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, getErrorMessage } from "../api";
import PageTitle from "../components/PageTitle";
import PageError from "../components/PageError";

export default function UserBehaviorPage() {
  const [reminder, setReminder] = useState<string>();
  const analysisQuery = useQuery({ queryKey: ["user-analysis"], queryFn: api.userAnalysis });
  const reminderMutation = useMutation({
    mutationFn: api.createReminder,
    onSuccess: (result) => setReminder(result.message),
    onError: (error) => message.error(getErrorMessage(error))
  });
  const analysis = analysisQuery.data;

  if (analysisQuery.isError) {
    return <PageError title="用户分析加载失败" description={getErrorMessage(analysisQuery.error)} onRetry={() => void analysisQuery.refetch()} />;
  }

  return (
    <div>
      <PageTitle eyebrow="CUSTOMER INSIGHTS" title="用户行为洞察" description="基于历史预约识别用户偏好，并生成更自然的个性化回访。" action={<Button loading={analysisQuery.isFetching} icon={<ReloadOutlined />} onClick={() => void analysisQuery.refetch()}>刷新分析</Button>} />
      {analysisQuery.isPending ? <Card><Skeleton active /></Card> : !analysis ? <Card><Empty description="暂无分析数据" /></Card> : (
        <>
          <Row gutter={[16, 16]} className="stats-row">
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="累计预约" value={analysis.total_appointments} suffix="次" prefix={<CalendarOutlined />} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="偏好时长" value={analysis.favorite_duration || 0} suffix="分钟" prefix={<ClockCircleOutlined />} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="距离上次预约" value={analysis.days_since_last_appointment ?? "--"} suffix={analysis.days_since_last_appointment != null ? "天" : ""} prefix={<UserOutlined />} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="回访建议" value={analysis.should_send_reminder ? "建议回访" : "暂不需要"} valueStyle={{ color: analysis.should_send_reminder ? "#e58b28" : "#34a853", fontSize: 22 }} /></Card></Col>
          </Row>
          <Row gutter={[18, 18]}>
            <Col xs={24} lg={15}>
              <Card title="偏好画像" className="data-card">
                <Descriptions column={1} bordered items={[
                  { key: "tech", label: "偏好技师", children: analysis.favorite_technician_name || "尚未形成明确偏好" },
                  { key: "service", label: "偏好服务", children: analysis.favorite_service ? <Tag color="purple">{analysis.favorite_service}</Tag> : "尚未形成明确偏好" },
                  { key: "duration", label: "偏好时长", children: analysis.favorite_duration ? `${analysis.favorite_duration} 分钟` : "暂无数据" },
                  { key: "status", label: "用户状态", children: analysis.total_appointments > 0 ? <Tag color="success">已有预约记录</Tag> : <Tag>新用户</Tag> }
                ]} />
              </Card>
            </Col>
            <Col xs={24} lg={9}>
              <Card className="reminder-card">
                <div className="reminder-icon"><HeartOutlined /></div>
                <Typography.Title level={3}>智能回访</Typography.Title>
                <Typography.Paragraph>结合用户历史偏好和技师空闲时段，由 AI 生成个性化提醒文案。</Typography.Paragraph>
                {analysis.total_appointments === 0 && <Alert type="info" showIcon message="完成预约后将获得更准确的分析" />}
                <Button type="primary" block size="large" icon={<BellOutlined />} loading={reminderMutation.isPending} onClick={() => reminderMutation.mutate()}>生成回访提醒</Button>
              </Card>
            </Col>
          </Row>
        </>
      )}
      <Modal title="AI 回访建议" open={Boolean(reminder)} onCancel={() => setReminder(undefined)} footer={<Button type="primary" onClick={() => setReminder(undefined)}>完成</Button>}>
        <Space direction="vertical" size="middle"><Tag color="purple">个性化文案</Tag><Typography.Paragraph className="reminder-copy">{reminder}</Typography.Paragraph></Space>
      </Modal>
    </div>
  );
}
