# 依赖：dashscope >= 1.23.9，pyaudio。
import os
import base64
import sys
import threading
import pyaudio
from dashscope.audio.qwen_omni import *
import dashscope

# 如果没有设置环境变量，请用您的 API Key 将下行替换为 dashscope.api_key = "sk-xxx"
# dashscope.api_key = "sk-3a6b1a3bd7124023a7ac7699d49c2caf"
dashscope.api_key = "sk-7344c0e7980c477ebdbc057c2460d08f"
voice = 'Ethan'

class MyCallback(OmniRealtimeCallback):
    """最简回调：建立连接时初始化扬声器，事件中直接播放返回音频。"""
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def on_open(self) -> None:
        # 连接建立后初始化 PyAudio 与扬声器(24k/mono/16bit)
        print('connection opened')
        try:
            self.ctx['pya'] = pyaudio.PyAudio()
            self.ctx['out'] = self.ctx['pya'].open(
                format=pyaudio.paInt16,
                channels=1,
                rate=24000,
                output=True
            )
            print('audio output initialized')
        except Exception as e:
            print('[Error] audio init failed: {}'.format(e))

    def on_close(self, close_status_code, close_msg) -> None:
        print('connection closed with code: {}, msg: {}'.format(close_status_code, close_msg))
        sys.exit(0)

    def on_event(self, response: str) -> None:
        try:
            t = response['type']
            print(t)
            handlers = {
                'session.created': lambda r: print('start session: {}'.format(r['session']['id'])),
                'conversation.item.input_audio_transcription.delta': lambda r: print('\rquestion: {}'.format(r.get('text', '') + r.get('stash', '')), end='', flush=True),
                'conversation.item.input_audio_transcription.completed': self._transcription_completed,
                'response.audio_transcript.delta': lambda r: print('llm text: {}'.format(r['delta'])),
                'response.audio.delta': self._play_audio,
                'response.done': self._response_done,
            }
            h = handlers.get(t)
            if h:
                h(response)
        except Exception as e:
            print('[Error] {}'.format(e))

    def _transcription_completed(self, response):
        print()
        self.ctx['transcription_done'].set()

    def _play_audio(self, response):
        # 直接解码base64并写入输出流进行播放
        if self.ctx['out'] is None:
            return
        try:
            data = base64.b64decode(response['delta'])
            self.ctx['out'].write(data)
        except Exception as e:
            print('[Error] audio playback failed: {}'.format(e))

    def _response_done(self, response):
        # 标记本轮对话完成，用于主循环等待
        if self.ctx['conv'] is not None:
            print('[Metric] response: {}, first text delay: {}, first audio delay: {}'.format(
                self.ctx['conv'].get_last_response_id(),
                self.ctx['conv'].get_last_first_text_delay(),
                self.ctx['conv'].get_last_first_audio_delay(),
            ))
        if self.ctx['resp_done'] is not None:
            self.ctx['resp_done'].set()

def shutdown_ctx(ctx):
    """安全释放音频与PyAudio资源。"""
    try:
        if ctx['out'] is not None:
            ctx['out'].close()
            ctx['out'] = None
    except Exception:
        pass
    try:
        if ctx['pya'] is not None:
            ctx['pya'].terminate()
            ctx['pya'] = None
    except Exception:
        pass


def stream_record_and_send(pya_inst, conversation, sample_rate=16000, chunk_size=3200):
    stop_evt = threading.Event()
    stream = pya_inst.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=sample_rate,
        input=True,
        frames_per_buffer=chunk_size
    )

    def _reader():
        while not stop_evt.is_set():
            try:
                data = stream.read(chunk_size, exception_on_overflow=False)
                conversation.append_audio(base64.b64encode(data).decode())
            except Exception:
                break

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    input()
    stop_evt.set()
    t.join(timeout=1.0)
    stream.close()


if __name__  == '__main__':
    print('Initializing ...')
    # 运行时上下文：存放音频与会话句柄
    ctx = {'pya': None, 'out': None, 'conv': None, 'resp_done': threading.Event(), 'transcription_done': threading.Event()}
    callback = MyCallback(ctx)
    conversation = OmniRealtimeConversation(
        # model='qwen3.5-omni-plus-realtime',
        model='qwen3-omni-flash-realtime',
        callback=callback,
        # 以下为北京地域url，若使用新加坡地域的模型，需将url替换为：wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime
        url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    try:
        conversation.connect()
    except Exception as e:
        print('[Error] connect failed: {}'.format(e))
        sys.exit(1)

    ctx['conv'] = conversation
    # 会话配置：启用文本+音频输出（禁用服务端VAD，改为手动录音）
    conversation.update_session(
        output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
        voice=voice,
        enable_input_audio_transcription=True,
        input_audio_transcription_model='qwen3-asr-flash-realtime',
        enable_turn_detection=False,
        instructions="你是个人助理小云，请你准确且友好地解答用户的问题，始终以乐于助人的态度回应。"
    )

    try:
        turn = 1
        while True:
            print(f"\n--- 第 {turn} 轮对话 ---")
            print("按 Enter 开始录音（输入 q 回车退出）...")
            user_input = input()
            if user_input.strip().lower() in ['q', 'quit']:
                print("用户请求退出...")
                break
            print("录音中... 再次按 Enter 停止。")
            if ctx['pya'] is None:
                ctx['pya'] = pyaudio.PyAudio()
            stream_record_and_send(ctx['pya'], conversation)

            ctx['transcription_done'].clear()
            ctx['resp_done'].clear()
            conversation.commit()
            ctx['transcription_done'].wait(timeout=10)
            print("等待模型回复...")
            conversation.create_response()
            ctx['resp_done'].wait()
            turn += 1
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        shutdown_ctx(ctx)
        print("程序退出")