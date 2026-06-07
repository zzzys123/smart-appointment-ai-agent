"""
意图分类准确率评估

评估 TaskClassifier 对不同用户输入的分类准确率。
输出每个类别的准确率和总体准确率。
"""

import asyncio
import sys
import os
import time
from collections import defaultdict

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.model_provider import create_chat_model
from agents.task_classification.task_classifier import TaskClassifier
from evaluation.test_cases import CLASSIFICATION_TEST_CASES


async def evaluate_classification():
    """运行意图分类评估"""
    print("=" * 60)
    print("📊 意图分类准确率评估")
    print("=" * 60)
    
    # 初始化分类器
    llm = create_chat_model(temperature=0)
    classifier = TaskClassifier(llm)
    
    # 统计数据
    total = len(CLASSIFICATION_TEST_CASES)
    correct = 0
    errors = []
    category_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    
    print(f"\n共 {total} 条测试用例，开始评估...\n")
    
    for i, (user_input, expected_category) in enumerate(CLASSIFICATION_TEST_CASES, 1):
        try:
            predicted = await classifier.classify_task(user_input)
            is_correct = predicted == expected_category
            
            category_stats[expected_category]["total"] += 1
            if is_correct:
                correct += 1
                category_stats[expected_category]["correct"] += 1
                status = "✅"
            else:
                status = "❌"
                errors.append({
                    "input": user_input,
                    "expected": expected_category,
                    "predicted": predicted
                })
            
            print(f"  [{i:02d}/{total}] {status} 输入: '{user_input[:30]}...' "
                  f"| 期望: {expected_category} | 预测: {predicted}")
            
        except Exception as e:
            print(f"  [{i:02d}/{total}] ⚠️  输入: '{user_input[:30]}...' | 异常: {e}")
            errors.append({
                "input": user_input,
                "expected": expected_category,
                "predicted": f"ERROR: {e}"
            })
    
    # 输出汇总结果
    print("\n" + "=" * 60)
    print("📈 评估结果汇总")
    print("=" * 60)
    
    accuracy = correct / total * 100 if total > 0 else 0
    print(f"\n  总体准确率: {correct}/{total} = {accuracy:.1f}%\n")
    
    print("  各类别准确率:")
    print(f"  {'类别':<15} {'正确/总数':<12} {'准确率':<10}")
    print(f"  {'-'*15} {'-'*12} {'-'*10}")
    
    for category in ['appointment', 'query', 'pay', 'statistics', 'other']:
        stats = category_stats[category]
        if stats["total"] > 0:
            cat_accuracy = stats["correct"] / stats["total"] * 100
            print(f"  {category:<15} {stats['correct']}/{stats['total']:<10} {cat_accuracy:.1f}%")
    
    # 输出错误详情
    if errors:
        print(f"\n  ❌ 错误详情 ({len(errors)} 条):")
        for err in errors:
            print(f"    - 输入: '{err['input']}'")
            print(f"      期望: {err['expected']} → 预测: {err['predicted']}")
    
    print("\n" + "=" * 60)
    
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "category_stats": dict(category_stats),
        "errors": errors
    }


if __name__ == "__main__":
    result = asyncio.run(evaluate_classification())
    
    # 退出码: 准确率低于 80% 则返回失败
    if result["accuracy"] < 80:
        print(f"\n⚠️  准确率 {result['accuracy']:.1f}% 低于 80% 阈值")
        sys.exit(1)
    else:
        print(f"\n✅ 准确率 {result['accuracy']:.1f}% 达标")
        sys.exit(0)
