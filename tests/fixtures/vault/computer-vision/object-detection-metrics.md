# Object Detection Metrics

## Precision and Recall

For object detection, whether a prediction is TP or FP depends on:
- predicted class
- IoU with the ground-truth box
- confidence threshold

An important point is that TP/FP counts are **not fixed** when constructing
the precision-recall curve.

Predictions are sorted by confidence, and the threshold is gradually lowered.
This produces different precision/recall values.

## Average Precision

AP is approximately the area under the precision-recall curve.

For multiple classes:

mAP = mean(AP for each class)

### Related
- [[IoU]]
- [[Confidence Score]]
- [[Evaluation Metrics]]
