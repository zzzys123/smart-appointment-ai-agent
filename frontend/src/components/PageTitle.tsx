import type { ReactNode } from "react";
import { Space, Typography } from "antd";

interface PageTitleProps {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}

export default function PageTitle({ eyebrow, title, description, action }: PageTitleProps) {
  return (
    <div className="page-heading">
      <div>
        <Typography.Text className="eyebrow">{eyebrow}</Typography.Text>
        <Typography.Title level={2}>{title}</Typography.Title>
        <Typography.Paragraph>{description}</Typography.Paragraph>
      </div>
      {action && <Space>{action}</Space>}
    </div>
  );
}
