import torch_directml, torch

n = torch_directml.device_count()
print(f"DirectML 디바이스 수: {n}")
for i in range(n):
    mem = torch_directml.gpu_memory(i)
    if isinstance(mem, (list, tuple)):
        mem_mb = mem[0] // 1024**2 if mem else 0
    else:
        mem_mb = mem // 1024**2
    print(f"  [{i}] VRAM: {mem_mb} MB")

print()
print("torch_directml API:", [x for x in dir(torch_directml) if not x.startswith("_")])

# 각 디바이스에 텐서 올려서 실제 작동 확인
for i in range(n):
    try:
        d = torch_directml.device(i)
        x = torch.ones(10, 10).to(d)
        y = x @ x
        print(f"  [{i}] 연산 테스트: OK")
    except Exception as e:
        print(f"  [{i}] 연산 테스트 실패: {e}")
