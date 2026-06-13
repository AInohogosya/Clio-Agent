#!/usr/bin/env python3
"""
VEXIS-CLI SDK Management Tool

Command-line tool for managing AI provider SDK dependencies.
Similar to the virtual environment setup, this handles SDK installation automatically.

Usage:
    python3 manage_sdks.py status          # Show SDK installation status
    python3 manage_sdks.py install         # Install all missing SDKs
    python3 manage_sdks.py install google  # Install specific SDK
    python3 manage_sdks.py install openai anthropic  # Install multiple SDKs
"""

import sys
import argparse
from pathlib import Path

# Add src to path (go up one level from peripherals/)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

try:
    from ai_agent.utils.sdk_installer import create_installer, PROVIDER_SDKS
    from ai_agent.external_integration.multi_provider_vision_client import MultiProviderVisionAPIClient
except ImportError as e:
    print(f"❌ Failed to import SDK management modules: {e}")
    print("Make sure you're running this from the VEXIS-CLI directory")
    sys.exit(1)

def show_status():
    """Show SDK installation status for all providers"""
    print("🔍 VEXIS-CLI SDK Status")
    print("=" * 50)
    
    installer = create_installer()
    installer.show_provider_status(list(PROVIDER_SDKS.keys()))
    
    # Show summary
    missing = installer.get_missing_sdks(list(PROVIDER_SDKS.keys()))
    available = len(PROVIDER_SDKS) - len(missing)
    
    print(f"\n📊 Summary: {available}/{len(PROVIDER_SDKS)} SDKs installed")
    
    if missing:
        print(f"\n🛠️  To install missing SDKs:")
        print("   python3 manage_sdks.py install")
        print("   python3 manage_sdks.py install <provider1> <provider2> ...")

def install_sdks(providers=None, interactive=True):
    """Install missing SDKs"""
    installer = create_installer()
    
    if providers is None:
        # Install all missing SDKs
        print("📦 Installing all missing SDKs...")
        results = installer.install_missing_sdks(list(PROVIDER_SDKS.keys()), interactive=interactive)
    else:
        # Install specific SDKs
        print(f"📦 Installing SDKs for: {', '.join(providers)}")
        results = {}
        for provider in providers:
            if provider not in PROVIDER_SDKS:
                print(f"❌ Unknown provider: {provider}")
                continue
            
            if installer.check_sdk_availability(provider):
                print(f"✅ {provider} SDK already installed")
                results[provider] = True
            else:
                success = installer.install_sdk(provider, interactive=interactive)
                results[provider] = success
    
    # Show results
    successful = sum(1 for success in results.values() if success)
    total = len(results)
    
    print(f"\n📊 Installation summary: {successful}/{total} successful")
    
    if successful < total:
        print("\n❌ Some installations failed. You may need to install them manually:")
        for provider, success in results.items():
            if not success and provider in PROVIDER_SDKS:
                sdk_info = PROVIDER_SDKS[provider]
                print(f"   {provider}: {sdk_info['install_command']}")
    else:
        print("🎉 All SDKs installed successfully!")

def test_providers():
    """Test provider initialization after SDK installation"""
    print("🧪 Testing Provider Initialization")
    print("=" * 50)
    
    client = MultiProviderVisionAPIClient()
    available = client.get_available_providers()
    
    print(f"✅ Successfully initialized {len(available)} providers:")
    for provider in available:
        print(f"   • {provider}")
    
    missing = set(PROVIDER_SDKS.keys()) - set(available)
    if missing:
        print(f"\n⚠️  {len(missing)} providers still unavailable:")
        for provider in missing:
            print(f"   • {provider}")
        
        print(f"\n💡 To install missing SDKs:")
        print("   python3 manage_sdks.py install")

def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(
        description="VEXIS-CLI SDK Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s status                    # Show SDK installation status
  %(prog)s install                   # Install all missing SDKs
  %(prog)s install google            # Install Google SDK only
  %(prog)s install openai anthropic  # Install multiple SDKs
  %(prog)s test                      # Test provider initialization
  
Available providers:
  google, openai, anthropic, xai, meta, mistral,
  microsoft, amazon, cohere, deepseek, groq, together
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Status command
    subparsers.add_parser('status', help='Show SDK installation status')
    
    # Install command
    install_parser = subparsers.add_parser('install', help='Install SDKs')
    install_parser.add_argument('providers', nargs='*', 
                               help='Specific providers to install (default: all missing)')
    install_parser.add_argument('--no-interactive', action='store_true',
                               help='Install without prompting for confirmation')
    
    # Test command
    subparsers.add_parser('test', help='Test provider initialization')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'status':
            show_status()
        elif args.command == 'install':
            interactive = not args.no_interactive
            install_sdks(args.providers if args.providers else None, interactive)
        elif args.command == 'test':
            test_providers()
    except KeyboardInterrupt:
        print("\n\n🛑 Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
