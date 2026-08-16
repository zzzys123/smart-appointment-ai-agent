import { ManOutlined, ReloadOutlined, StarFilled, TeamOutlined, WomanOutlined } from "@ant-design/icons";
import { Avatar, Button, Card, Col, Empty, Row, Skeleton, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, getErrorMessage } from "../api";
import PageTitle from "../components/PageTitle";
import PageError from "../components/PageError";

const gradients = ["violet", "blue", "coral", "green", "amber"];

export default function TechniciansPage() {
  const techniciansQuery = useQuery({
    queryKey: ["technicians"],
    queryFn: api.listTechnicians
  });
  const technicians = techniciansQuery.data || [];

  if (techniciansQuery.isError) {
    return <PageError title="技师数据加载失败" description={getErrorMessage(techniciansQuery.error)} onRetry={() => void techniciansQuery.refetch()} />;
  }

  return (
    <div>
      <PageTitle eyebrow="SERVICE TEAM" title="技师团队" description="查看技师专长与基础信息，为智能匹配提供清晰依据。" action={<Button loading={techniciansQuery.isFetching} icon={<ReloadOutlined />} onClick={() => void techniciansQuery.refetch()}>刷新数据</Button>} />
      <Card className="team-summary">
        <Space size="large"><Avatar size={48} icon={<TeamOutlined />} /><div><Typography.Title level={4}>{technicians.length} 位专业技师</Typography.Title><Typography.Text type="secondary">系统会综合服务专长、性别偏好和空闲时间进行推荐</Typography.Text></div></Space>
      </Card>
      {techniciansQuery.isPending ? <Row gutter={[18, 18]}>{[1, 2, 3, 4].map((item) => <Col xs={24} md={12} xl={8} key={item}><Card><Skeleton active avatar /></Card></Col>)}</Row> : technicians.length === 0 ? <Card><Empty description="暂无技师数据" /></Card> : (
        <Row gutter={[18, 18]}>
          {technicians.map((technician, index) => (
            <Col xs={24} md={12} xl={8} key={technician.id}>
              <Card className="technician-card" hoverable>
                <div className={`technician-cover ${gradients[index % gradients.length]}`}><Avatar size={68}>{technician.name.slice(-2)}</Avatar><Tag color="success">可预约</Tag></div>
                <Typography.Title level={4}>{technician.name}</Typography.Title>
                <Space><Tag icon={technician.gender === "女" ? <WomanOutlined /> : <ManOutlined />}>{technician.gender || "未设置"}</Tag><Tag icon={<StarFilled />} color="gold">专业技师</Tag></Space>
                <div className="strength-block"><Typography.Text type="secondary">擅长项目</Typography.Text><Typography.Paragraph>{technician.strength || "综合理疗与放松服务"}</Typography.Paragraph></div>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}
