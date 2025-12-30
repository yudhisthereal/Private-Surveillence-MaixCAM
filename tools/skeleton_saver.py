import csv
import os

class SkeletonSaver2D:
    def __init__(self):
        self.data_buffer = []
        self.log_dir = "/root/extracted-skeleton-2d"
        self.log_filename = ""
    
    def start_new_log(self, log_filename):
        self.log_filename = log_filename

    def add_keypoints(self, frame_id, person_id, keypoints_flat, fall_status=0):
        if not keypoints_flat:
            # print(f"[DEBUG] No keypoints for frame {frame_id}")
            return

        # Convert flat list to pairs
        pairs = [(keypoints_flat[i], keypoints_flat[i+1]) for i in range(0, len(keypoints_flat), 2)]
        num_points = len(pairs)
        flat_coords = keypoints_flat  # already flattened

        # print(f"[DEBUG] Frame {frame_id} – saving {num_points} keypoints")
        self.data_buffer.append([frame_id, person_id] + flat_coords + [fall_status])

    def save_to_csv(self):
        if not self.log_filename:
            # print("[DEBUG] specify log file name first!")
            return
        
        """Save buffered keypoints to CSV using video filename as base"""
        base_name = os.path.splitext(self.log_filename)[0]
        csv_filename = os.path.join(self.log_dir, f"{base_name}.csv")
            

        # Create directory if it doesn't exist
        os.makedirs(self.log_dir, exist_ok=True)

        if not self.data_buffer:
            print("[WARNING] No keypoints to save.")
            return

        num_kp = (len(self.data_buffer[0]) - 3) // 2
        header = ['frame_id', 'person_id'] + [f'{c}{i}' for i in range(num_kp) for c in ['x', 'y']] + ['fall_status']

        with open(csv_filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(self.data_buffer)

        # print(f"[INFO] Saved keypoints to {csv_filename}")
        self.data_buffer = []  # Clear buffer after save