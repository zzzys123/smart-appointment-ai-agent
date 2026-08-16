import { useMemo } from "react";
import { CalendarOutlined, ClockCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Avatar, Button, Card, Empty, Progress, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { api, getErrorMessage } from "../api";
import PageTitle from "../components/PageTitle";
import PageError from "../components/PageError";
import type { ScheduleItem, Technician } from "../types";

interface TechnicianSchedule {
  technician: Technician;
  schedules: ScheduleItem[];
}

export default function SchedulePage() {
  const scheduleQuery = useQuery({
    queryKey: ["today-schedules"],
    queryFn: async () => {
      const technicians = await api.listTechnicians();
      return Promise.all(technicians.map(async (technician) => ({ technician, schedules: await api.technicianSchedule(technician.id) })));
    }
  });
  const data: TechnicianSchedule[] = scheduleQuery.data || [];

  const totals = useMemo(() => data.reduce((result, item) => ({ slots: result.slots + item.schedules.length, busy: result.busy + item.schedules.filter((slot) => slot.status === "busy").length }), { slots: 0, busy: 0 }), [data]);

  if (scheduleQuery.isError) {
    return <PageError title="排班数据加载失败" description={getErrorMessage(scheduleQuery.error)} onRetry={() => void scheduleQuery.refetch()} />;
  }

  return (
    <div>
      <PageTitle eyebrow="DAILY OPERATIONS" title="今日排班" description={`${dayjs().format("YYYY年M月D日 dddd")} · 实时查看每位技师的服务时段`} action={<Button loading={scheduleQuery.isFetching} icon={<ReloadOutlined />} onClick={() => void scheduleQuery.refetch()}>刷新排班</Button>} />
      <Alert className="schedule-alert" type="info" showIcon icon={<CalendarOutlined />} message={`今日共 ${totals.slots} 个排班时段，${totals.busy} 个时段已占用`} />
      {scheduleQuery.isPending ? <div className="center-loading"><Spin size="large" /></div> : data.length === 0 ? <Card><Empty description="暂无排班数据" /></Card> : (
        <div className="schedule-grid">
          {data.map(({ technician, schedules }) => {
            const busy = schedules.filter((item) => item.status === "busy").length;
            const occupancy = schedules.length ? Math.round((busy / schedules.length) * 100) : 0;
            return (
              <Card key={technician.id} className="schedule-card">
                <div className="schedule-card-head"><Space><Avatar>{technician.name.slice(-2)}</Avatar><div><Typography.Text strong>{technician.name}</Typography.Text><span>{technician.strength}</span></div></Space><Tag color={occupancy > 70 ? "warning" : "success"}>{occupancy}% 已约</Tag></div>
                <Progress percent={occupancy} showInfo={false} strokeColor={{ "0%": "#635bff", "100%": "#47c8ff" }} />
                {schedules.length ? (
                  <Timeline items={schedules.map((slot) => ({ color: slot.status === "busy" ? "red" : "green", children: <div className="slot-row"><Space><ClockCircleOutlined /><span>{slot.start_time} – {slot.end_time}</span></Space><Tag color={slot.status === "busy" ? "error" : "success"}>{slot.status === "busy" ? "已预约" : "可预约"}</Tag></div> }))} />
                ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="今日暂无排班" />}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
