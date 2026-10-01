# Local LLM Quantization

Quantization reduces the memory required to store model weights.

## Common options

### Q4
- Low VRAM usage
- Allows larger context or larger models
- Some quality degradation

### Q5
Seems like a reasonable middle ground between memory and quality.

### Q6
Higher quality, but the additional VRAM usage may become significant for
large models.

## Things to investigate

- How noticeable is the difference between Q4 and Q6 in actual use?
- Does quantization affect coding more than ordinary writing?
- How much VRAM should be reserved for KV cache?
- [[KV Cache]]
- [[Context Length]]
