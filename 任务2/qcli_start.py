"""
音频客户端启动脚本

使用方法:
    python qcli_start.py [host] [port] [nickname]
    
示例:
    python qcli_start.py
    python qcli_start.py localhost 8880
    python qcli_start.py localhost 8880 Alice
    python qcli_start.py 192.168.1.100 8880 Bob
"""

from qcli import main

if __name__ == '__main__':
    print("="*50)
    print("音频通信客户端")
    print("="*50)
    print()
    main()
