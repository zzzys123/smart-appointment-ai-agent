"""
一键运行所有评估

运行全部三项评估并输出综合报告：
1. 意图分类准确率
2. RAG 检索召回率
3. 预约流程完成率

用法:
    python -m evaluation.run_all          # 运行全部
    python -m evaluation.run_all --fast   # 只运行分类评估（最快）
"""

import asyncio
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


async def run_all_evaluations(fast_mode: bool = False):
    """运行所有评估"""
    start_time = time.time()
    
    print("\n" + "🚀" * 30)
    print("       Smart Appointment AI Agent - 系统评估报告")
    print(f"       评估时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("🚀" * 30 + "\n")
    
    results = {}
    
    # 1. 意图分类评估
    print("\n" + "━" * 60)
    print("  [1/3] 意图分类准确率评估")
    print("━" * 60 + "\n")
    
    from evaluation.eval_classification import evaluate_classification
    results["classification"] = await evaluate_classification()
    
    if fast_mode:
        print("\n⏩ Fast 模式：跳过 RAG 和预约流程评估")
        results["rag"] = None
        results["appointment"] = None
    else:
        # 2. RAG 检索评估
        print("\n\n" + "━" * 60)
        print("  [2/3] RAG 检索召回率评估")
        print("━" * 60 + "\n")
        
        from evaluation.eval_rag_retrieval import evaluate_rag_retrieval
        results["rag"] = await evaluate_rag_retrieval(top_k=3)
        
        # 3. 预约流程评估
        print("\n\n" + "━" * 60)
        print("  [3/3] 预约流程完成率评估")
        print("━" * 60 + "\n")
        
        from evaluation.eval_appointment_flow import evaluate_appointment_flow
        results["appointment"] = await evaluate_appointment_flow()
    
    # 综合报告
    elapsed = time.time() - start_time
    print_summary_report(results, elapsed)
    
    return results


def print_summary_report(results: dict, elapsed: float):
    """打印综合评估报告"""
    print("\n\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + "       📋 综合评估报告".center(50) + "        ║")
    print("╠" + "═" * 58 + "╣")
    
    # 分类结果
    cls_result = results.get("classification", {})
    cls_accuracy = cls_result.get("accuracy", 0) if cls_result else 0
    cls_icon = "✅" if cls_accuracy >= 80 else "⚠️" if cls_accuracy >= 60 else "❌"
    print(f"║  {cls_icon} 意图分类准确率:    {cls_accuracy:>5.1f}%"
          f"   ({cls_result.get('correct', 0)}/{cls_result.get('total', 0)})".ljust(29) + "║")
    
    # RAG 结果
    rag_result = results.get("rag")
    if rag_result:
        rag_recall = rag_result.get("recall_rate", 0)
        rag_icon = "✅" if rag_recall >= 70 else "⚠️" if rag_recall >= 50 else "❌"
        print(f"║  {rag_icon} RAG Top-3 召回率:  {rag_recall:>5.1f}%"
              f"   ({rag_result.get('hits', 0)}/{rag_result.get('total', 0)})".ljust(29) + "║")
    else:
        print("║  ⏩ RAG 检索召回率:     (跳过)".ljust(59) + "║")
    
    # 预约流程结果
    appt_result = results.get("appointment")
    if appt_result:
        appt_accuracy = appt_result.get("accuracy", 0)
        appt_icon = "✅" if appt_accuracy >= 60 else "⚠️" if appt_accuracy >= 40 else "❌"
        print(f"║  {appt_icon} 预约流程准确率:    {appt_accuracy:>5.1f}%"
              f"   ({appt_result.get('correct', 0)}/{appt_result.get('total', 0)})".ljust(29) + "║")
    else:
        print("║  ⏩ 预约流程准确率:     (跳过)".ljust(59) + "║")
    
    print("╠" + "═" * 58 + "╣")
    print(f"║  ⏱️  总耗时: {elapsed:.1f}s".ljust(60) + "║")
    print("╚" + "═" * 58 + "╝")
    
    # 面试话术建议
    print("\n")
    print("💡 面试话术参考:")
    print("─" * 40)
    
    if cls_accuracy > 0:
        print(f"  \"我对系统做了量化评估，意图分类准确率达到 {cls_accuracy:.0f}%，")
    
    if rag_result and rag_result.get("recall_rate", 0) > 0:
        print(f"   RAG 检索 Top-3 召回率 {rag_result['recall_rate']:.0f}%，")
    
    if appt_result and appt_result.get("accuracy", 0) > 0:
        print(f"   预约流程场景覆盖准确率 {appt_result['accuracy']:.0f}%。\"")
    
    print()


if __name__ == "__main__":
    fast_mode = "--fast" in sys.argv
    results = asyncio.run(run_all_evaluations(fast_mode))
    
    # 综合判断退出码
    all_pass = True
    cls = results.get("classification", {})
    if cls and cls.get("accuracy", 0) < 80:
        all_pass = False
    
    rag = results.get("rag")
    if rag and rag.get("recall_rate", 0) < 70:
        all_pass = False
    
    appt = results.get("appointment")
    if appt and appt.get("accuracy", 0) < 60:
        all_pass = False
    
    sys.exit(0 if all_pass else 1)
