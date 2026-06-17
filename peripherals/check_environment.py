#!/usr/bin/env python3
"""
Standalone Environment Check Script for Clio-Agent-1
Usage: python3 check_environment.py [--fix]
"""

import sys
import os

# Add src to path for imports (go up one level from peripherals/)
script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.join(script_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from ai_agent.utils.environment_detector import detect_and_plan, AdaptiveExecutor


def main():
    fix_mode = "--fix" in sys.argv
    
    print("=" * 70)
    print("🔍 Clio-Agent-1 Environment Detection and Setup")
    print("=" * 70)
    
    # Run detection and create plan
    env_info, executor = detect_and_plan()
    
    # Show recommendations
    recommendations = executor.get_recommendations()
    
    print("\n" + "=" * 70)
    print("📊 Environment Status Summary")
    print("=" * 70)
    print(f"  Ollama Available:     {'✓ Yes' if env_info.ollama_available else '✗ No'}")
    print(f"  Ollama Version:       {env_info.ollama_version or 'N/A'}")
    print(f"  Can Use Cloud Models: {'✓ Yes' if env_info.can_use_cloud_models else '✗ No'}")
    print(f"  Local Models:           {len(env_info.ollama_local_models)} installed")
    print(f"  Cloud Models:           {len(env_info.ollama_cloud_models)} installed")
    print(f"  Recommended Provider: {env_info.recommended_provider}")
    
    # Execute fix plan if requested
    if fix_mode and executor.execution_plan:
        print(f"\n🔧 Fix mode enabled - executing {len(executor.execution_plan)} steps\n")
        success = executor.execute_plan(interactive=True)
        if success:
            print("\n✓ Setup completed successfully!")
            print("\nYou can now run: python3 run.py \"your command\"")
        else:
            print("\n⚠ Setup completed with some issues.")
            print("Please review the errors above and try again.")
    elif executor.execution_plan:
        print(f"\n💡 {len(executor.execution_plan)} setup steps available")
        print("\nRun with --fix to automatically address these issues:")
        print(f"  python3 {sys.argv[0]} --fix")
    else:
        print("\n✓ Environment is ready!")
        print("\nYou can run: python3 run.py \"your command\"")
    
    return 0 if not env_info.warnings else 1


if __name__ == "__main__":
    sys.exit(main())
