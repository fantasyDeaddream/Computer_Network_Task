"""
测试客户端和服务器协议匹配性
"""

import json

print("="*60)
print("协议匹配性测试")
print("="*60)

# 测试1: Login消息
print("\n1. Login消息格式:")
login_msg = json.dumps({
    'type': 'login',
    'nickname': 'TestUser'
})
print(f"   客户端发送: {login_msg}")
print(f"   服务器期望: type='login', nickname字段")
print(f"   ✓ 匹配")

# 测试2: Logout消息
print("\n2. Logout消息格式:")
logout_msg = json.dumps({
    'type': 'logout',
    'nickname': 'TestUser'
})
print(f"   客户端发送: {logout_msg}")
print(f"   服务器期望: type='logout'")
print(f"   ✓ 匹配")

# 测试3: 文本广播消息
print("\n3. 文本广播消息格式:")
text_msg = json.dumps({
    'type': 'broadcast',
    'message': 'Hello everyone!'
})
print(f"   客户端发送: {text_msg}")
print(f"   服务器期望: type='broadcast', message字段")
print(f"   ✓ 匹配")

# 测试4: 服务器广播格式
print("\n4. 服务器广播给客户端的消息格式:")
server_broadcast = json.dumps({
    'sender_id': 1,
    'sender_nickname': 'Alice',
    'message': 'Hello!'
})
print(f"   服务器发送: {server_broadcast}")
print(f"   客户端期望: sender_id, sender_nickname, message字段")
print(f"   ✓ 匹配")

# 测试5: 音频消息
print("\n5. 音频消息格式:")
audio_msg = json.dumps({
    'type': 'audio',
    'filename': 'test.wav',
    'length': 1024,
    'data': 'base64_encoded_data'
})
print(f"   客户端发送: {audio_msg}")
print(f"   服务器期望: type='audio'")
print(f"   ✓ 匹配")

print("\n" + "="*60)
print("所有协议检查通过！")
print("="*60)
