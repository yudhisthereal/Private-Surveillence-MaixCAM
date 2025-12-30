from maix import video, time, image

class VideoRecorder:
    def __init__(self):
        self.encoder = None
        self.filename = None
        self.width = None
        self.height = None
        self.is_active = False

    def start(self, filename, width, height):
        """
        Start recording into a new file.
        """
        self.filename = filename
        self.width = width
        self.height = height
        self.encoder = video.Encoder(filename, width, height)
        self.is_active = True

    def add_frame(self, img):
        """
        Add a frame to the recording. Converts to YVU420SP if needed.
        """
        if self.encoder is None:
            raise RuntimeError("VideoRecorder not started. Call start() first.")

        # Ensure format is YVU420SP
        if img.format() != image.Format.FMT_YVU420SP:
            img = img.to_format(image.Format.FMT_YVU420SP)

        self.encoder.encode(img)

    def end(self):
        """
        Finish recording and clean up.
        """
        if self.encoder is not None:
            del self.encoder
            self.encoder = None
            self.filename = None
            self.width = None
            self.height = None
            self.is_active = False
