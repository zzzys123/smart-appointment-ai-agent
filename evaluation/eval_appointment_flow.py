"""
预约流程完成率评估

评估预约 Agent 的多轮对话能力：
- 信息提取准确性（能否正确解析用户输入中的预约字段）
- 缺失信息追问（能否正确识别缺少哪些字段并追问）
- 完整预约的成功率
"""

import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from agents.appointment_agent import AppointmentAgent
from evaluation.test_cases import APPOINTMENT_FLOW_TEST_CASES


async def evaluate_single_flow(test_case: dict, case_index: int) -> dict:
    """评估单个预约流程场景"""
    description = test_case["description"]
    turns = test_case["turns"]
    expected_outcome = test_case["expected_outcome"]
    
    # 为每个测试用例创建新的 Agent 实例
    agent = AppointmentAgent(session_id=f"eval_session_{case_index}")
    
    all_responses = []
    final_status = "unknown"
    
    try:
        for turn_idx, user_input in enumerate(turns):
            response_tokens = []
            async for token in agent.run_stream(user_input=user_input):
                response_tokens.append(token)
            
            full_response = "".join(response_tokens)
            all_responses.append({
                "turn": turn_idx + 1,
                "input": user_input,
                "response": full_response[:200]  # 截断过长的响应
            })
        
        # 判断最终状态
        last_response = all_responses[-1]["response"] if all_responses else ""
        
        if "预约成功" in last_response or agent.finished:
            final_status = "success"
        elif "[THOUGHT]" in last_response and "不完整" in last_response:
            final_status = "ask_more_info"
        elif any(kw in last_response for kw in ["缺少", "请问", "您希望", "请补充"]):
            final_status = "ask_more_info"
        elif "无关" in last_response or "归类机器人" in last_response:
            final_status = "reject"
        elif agent.appointment_history.get("start_time") and agent.appointment_history.get("project"):
            # 有部分信息但没有预约成功，可能在等更多信息
            if agent.finished:
                final_status = "success"
            else:
                final_status = "ask_more_info"
        else:
            final_status = "ask_more_info"
        
        # 判断是否符合期望
        if expected_outcome == "success_or_ask":
            # 弹性期望：LLM 可能推断默认值直接成功，也可能追问
            is_correct = final_status in ("success", "ask_more_info")
        elif expected_outcome == "reject_or_ask":
            # 弹性期望：单独调用AppointmentAgent时可能无法识别unrelated
            is_correct = final_status in ("reject", "ask_more_info")
        else:
            is_correct = final_status == expected_outcome
        
        return {
            "description": description,
            "expected_outcome": expected_outcome,
            "actual_outcome": final_status,
            "is_correct": is_correct,
            "turns_count": len(turns),
            "responses": all_responses,
            "appointment_history": dict(agent.appointment_history) if hasattr(agent, 'appointment_history') else {}
        }
        
    except Exception as e:
        return {
            "description": description,
            "expected_outcome": expected_outcome,
            "actual_outcome": "error",
            "is_correct": False,
            "error": str(e),
            "turns_count": len(turns),
            "responses": all_responses
        }


async def evaluate_appointment_flow():
    """运行预约流程评估"""
    print("=" * 60)
    print("📊 预约流程完成率评估")
    print("=" * 60)
    
    total = len(APPOINTMENT_FLOW_TEST_CASES)
    correct = 0
    results = []
    
    print(f"\n共 {total} 个场景，开始评估...\n")
    
    for i, test_case in enumerate(APPOINTMENT_FLOW_TEST_CASES, 1):
        description = test_case["description"]
        print(f"  [{i:02d}/{total}] 场景: {description}")
        
        result = await evaluate_single_flow(test_case, i)
        results.append(result)
        
        if result["is_correct"]:
            correct += 1
            status = "✅"
        else:
            status = "❌"
        
        print(f"         {status} 期望: {result['expected_outcome']} | "
              f"实际: {result['actual_outcome']} | "
              f"轮数: {result['turns_count']}")
        
        if not result["is_correct"] and "error" in result:
            print(f"         ⚠️  错误: {result['error']}")
    
    # 输出汇总结果
    print("\n" + "=" * 60)
    print("📈 评估结果汇总")
    print("=" * 60)
    
    accuracy = correct / total * 100 if total > 0 else 0
    print(f"\n  流程判断准确率: {correct}/{total} = {accuracy:.1f}%")
    
    # 按期望结果分组统计
    outcome_stats = {}
    for r in results:
        expected = r["expected_outcome"]
        if expected not in outcome_stats:
            outcome_stats[expected] = {"total": 0, "correct": 0}
        outcome_stats[expected]["total"] += 1
        if r["is_correct"]:
            outcome_stats[expected]["correct"] += 1
    
    print(f"\n  各场景类型准确率:")
    print(f"  {'场景类型':<15} {'正确/总数':<12} {'准确率':<10}")
    print(f"  {'-'*15} {'-'*12} {'-'*10}")
    for outcome_type, stats in outcome_stats.items():
        type_accuracy = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  {outcome_type:<15} {stats['correct']}/{stats['total']:<10} {type_accuracy:.1f}%")
    
    # 输出失败的场景
    failures = [r for r in results if not r["is_correct"]]
    if failures:
        print(f"\n  ❌ 失败的场景 ({len(failures)} 个):")
        for r in failures:
            print(f"    - {r['description']}")
            print(f"      期望: {r['expected_outcome']} → 实际: {r['actual_outcome']}")
            if r.get("appointment_history"):
                history = r["appointment_history"]
                filled = {k: v for k, v in history.items() if v and v != "未知"}
                print(f"      已收集字段: {filled}")
    
    print("\n" + "=" * 60)
    
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "outcome_stats": outcome_stats,
        "details": results
    }


if __name__ == "__main__":
    result = asyncio.run(evaluate_appointment_flow())
    
    if result["accuracy"] < 60:
        print(f"\n⚠️  准确率 {result['accuracy']:.1f}% 低于 60% 阈值")
        sys.exit(1)
    else:
        print(f"\n✅ 准确率 {result['accuracy']:.1f}% 达标")
        sys.exit(0)
