import { CloudServerOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Space, Typography } from "antd";

interface PageErrorProps {
  title: string;
  description: string;
  onRetry: () => void;
}

export default function PageError({ title, description, onRetry }: PageErrorProps) {
  return (
    <Card className="page-error-card">
      <Space direction="vertical" size="large" align="center">
        <div className="page-error-icon"><CloudServerOutlined /></div>
        <div>
          <Typography.Title level={4}>{title}</Typography.Title>
          <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
        </div>
        <Alert
          type="info"
          showIcon
          message="请确认 FastAPI 已在 127.0.0.1:8000 运行"
        />
        <Button type="primary" icon={<ReloadOutlined />} onClick={onRetry}>重新请求</Button>
      </Space>
    </Card>
  );
}
