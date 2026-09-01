import { lazy, Suspense, useMemo, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  BarChartOutlined,
  BookOutlined,
  CalendarOutlined,
  DashboardOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  RobotOutlined,
  TeamOutlined
} from "@ant-design/icons";
import { Avatar, Button, Layout, Menu, Skeleton, Space, Tag, Typography } from "antd";

const ChatPage = lazy(() => import("./pages/ChatPage"));
const KnowledgePage = lazy(() => import("./pages/KnowledgePage"));
const SchedulePage = lazy(() => import("./pages/SchedulePage"));
const TechniciansPage = lazy(() => import("./pages/TechniciansPage"));
const UserBehaviorPage = lazy(() => import("./pages/UserBehaviorPage"));
const ObservabilityPage = lazy(() => import("./pages/ObservabilityPage"));

const { Header, Sider, Content } = Layout;

const navItems = [
  { key: "/chat", icon: <MessageOutlined />, label: <Link to="/chat">AI 咨询预约</Link> },
  { key: "/knowledge", icon: <BookOutlined />, label: <Link to="/knowledge">知识库管理</Link> },
  { key: "/technicians", icon: <TeamOutlined />, label: <Link to="/technicians">技师管理</Link> },
  { key: "/schedule", icon: <CalendarOutlined />, label: <Link to="/schedule">今日排班</Link> },
  { key: "/behavior", icon: <BarChartOutlined />, label: <Link to="/behavior">用户洞察</Link> },
  { key: "/observability", icon: <DashboardOutlined />, label: <Link to="/observability">系统观测</Link> }
];

export default function App() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const selectedKey = useMemo(
    () => navItems.find((item) => location.pathname.startsWith(item.key))?.key || "/chat",
    [location.pathname]
  );

  return (
    <Layout className="app-shell">
      <Sider
        width={248}
        collapsedWidth={76}
        collapsed={collapsed}
        breakpoint="lg"
        onBreakpoint={setCollapsed}
        className="app-sider"
      >
        <div className="brand">
          <Avatar size={38} icon={<RobotOutlined />} className="brand-avatar" />
          {!collapsed && (
            <div>
              <Typography.Text className="brand-name">智约 AI</Typography.Text>
              <span className="brand-caption">Agent Workspace</span>
            </div>
          )}
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={navItems} className="main-menu" />
        {!collapsed && (
          <div className="sider-status">
            <Space>
              <span className="status-dot" />
              <Typography.Text>服务运行中</Typography.Text>
            </Space>
            <span>FastAPI · LangGraph</span>
          </div>
        )}
      </Sider>
      <Layout>
        <Header className="app-header">
          <Button
            type="text"
            aria-label={collapsed ? "展开菜单" : "收起菜单"}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
          />
          <Space size="middle">
            <Tag color="purple" bordered={false}>React 工作台</Tag>
            <Avatar>访</Avatar>
          </Space>
        </Header>
        <Content className="app-content">
          <Suspense fallback={<div className="route-loader"><Skeleton active paragraph={{ rows: 8 }} /></div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/chat" replace />} />
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/technicians" element={<TechniciansPage />} />
              <Route path="/schedule" element={<SchedulePage />} />
              <Route path="/behavior" element={<UserBehaviorPage />} />
              <Route path="/observability" element={<ObservabilityPage />} />
              <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  );
}
