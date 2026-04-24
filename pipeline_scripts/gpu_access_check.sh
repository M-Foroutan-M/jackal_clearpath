#!/bin/bash
echo "=== GPU Check ==="
nvidia-smi || echo "❌ nvidia-smi failed"

echo -e "\n=== CUDA Check ==="
nvcc --version || echo "❌ CUDA not found"

echo -e "\n=== DeepStream Check ==="
deepstream-app --version-all || echo "❌ DeepStream not found"

echo -e "\n=== GStreamer NVIDIA Plugins ==="
gst-inspect-1.0 nvvideoconvert > /dev/null 2>&1 && echo "✅ nvvideoconvert" || echo "❌ nvvideoconvert"
gst-inspect-1.0 nvstreammux > /dev/null 2>&1 && echo "✅ nvstreammux" || echo "❌ nvstreammux"

echo -e "\n=== Python Check ==="
python3 -c "import gi; print('✅ Python GI available')" || echo "❌ Python GI failed"