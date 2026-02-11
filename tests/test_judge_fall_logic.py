
import sys
import os
import queue
from types import SimpleNamespace

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pose.judge_fall import get_fall_info

# Mock classes/data
class MockDet:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

def create_mock_targets(num_targets=2):
    targets = {
        "id": [i for i in range(num_targets)],
        "bbox": [queue.Queue() for _ in range(num_targets)],
        "points": [queue.Queue() for _ in range(num_targets)]
    }
    return targets

def test_independent_counters():
    print("Testing Independent Counters...")
    targets = create_mock_targets(2)
    
    # State for two tracks
    state1 = {}
    state2 = {}
    
    fall_param = {"v_bbox_y": 0.1}
    fps = 30
    queue_size = 5
    
    # Simulate history for track 0 (Fall behavior)
    # Previous: y=100, h=100
    targets["bbox"][0].put([100, 100, 50, 100])
    # Points: Full body visible (dummy values > 1)
    # 17 keypoints * 2 = 34 values
    full_body_points = [10.0] * 34 
    targets["points"][0].put(full_body_points)
    
    # Current for track 0: y=150 (moved down), h=80 (shrunk) -> FALL
    det0 = MockDet(100, 150, 50, 80)
    
    # Simulate history for track 1 (Stable behavior)
    # Previous: y=100, h=100
    targets["bbox"][1].put([200, 100, 50, 100])
    targets["points"][1].put(full_body_points) # Full body
    
    # Current for track 1: y=100 (no move), h=100 -> NO FALL
    det1 = MockDet(200, 100, 50, 100)
    
    # Run detection for track 0
    res0 = get_fall_info(det0, targets, 0, fall_param, queue_size, fps, pose_data={'label': 'standing'}, state=state1)
    state1 = res0[4] # update state
    
    # Run detection for track 1
    res1 = get_fall_info(det1, targets, 1, fall_param, queue_size, fps, pose_data={'label': 'standing'}, state=state2)
    state2 = res1[4] # update state
    
    print(f"Track 0 Counter: {state1['counter_bbox_only']}")
    print(f"Track 1 Counter: {state2['counter_bbox_only']}")
    
    if state1['counter_bbox_only'] > state2['counter_bbox_only']:
        print("PASS: Track 0 counter incremented independently of Track 1.")
    else:
        print("FAIL: Counters are not independent or logic failed.")

def test_safeguard():
    print("\nTesting Safeguard (Too Close)...")
    targets = create_mock_targets(1)
    state = {}
    fall_param = {"v_bbox_y": 0.1}
    fps = 30
    queue_size = 5
    
    # Case 1: Full Body Visible -> Should process
    print("Case 1: Full Body Visible")
    targets["bbox"][0].put([100, 100, 50, 100])
    full_body = [10.0] * 34
    targets["points"][0].put(full_body)
    
    det = MockDet(100, 150, 50, 80) # Fall movement
    
    res = get_fall_info(det, targets, 0, fall_param, queue_size, fps, pose_data={'label': 'standing'}, state=state)
    state = res[4]
    print(f"Result (Full Body): FallDetected={res[0]}, Counter={state['counter_bbox_only']}")
    
    if state['counter_bbox_only'] > 0:
        print("PASS: Fall detected for full body.")
    else:
        print("FAIL: Fall NOT detected for full body.")
        
    # Case 2: Legs Missing (Too Close) -> Should abort
    print("Case 2: Legs Missing (Too Close)")
    # Reset state
    state = None
    
    targets = create_mock_targets(1)
    targets["bbox"][0].put([100, 100, 50, 100])
    
    # Create partial body points
    # Indices:
    # Left: Eye(1->0,1), Shoulder(5->8,9), Hip(11->20,21), Knee(13->24,25)
    # Right: Eye(2->2,3), Shoulder(6->10,11), Hip(12->22,23), Knee(14->26,27)
    # Let's make Knees (13, 14) invisible (0.0)
    partial_body = [10.0] * 34
    # Zero out knees
    partial_body[24] = 0.0 # Left Knee X
    partial_body[25] = 0.0 # Left Knee Y
    partial_body[26] = 0.0 # Right Knee X
    partial_body[27] = 0.0 # Right Knee Y
    
    targets["points"][0].put(partial_body)
    
    det = MockDet(100, 150, 50, 80) # Fall movement
    
    res = get_fall_info(det, targets, 0, fall_param, queue_size, fps, pose_data={'label': 'standing'}, state=state)
    state = res[4]
    print(f"Result (Partial Body): FallDetected={res[0]}, Counter={state.get('counter_bbox_only', 0)}")
    
    if state.get('counter_bbox_only', 0) == 0:
        print("PASS: Safeguard triggered, counter not incremented.")
    else:
        print("FAIL: Safeguard FAILED, counter incremented.")

if __name__ == "__main__":
    test_independent_counters()
    test_safeguard()
