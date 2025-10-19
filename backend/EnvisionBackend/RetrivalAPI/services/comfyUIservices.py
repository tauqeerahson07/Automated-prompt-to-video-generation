import websocket 
import uuid
import json
import urllib.request
import urllib.parse

save_image_websocket = 'SaveImageWebsocket'
server_address = "127.0.0.1:8188"
client_id = str(uuid.uuid4())



def get_prompt_with_workflow(input):
    prompt_text = '''
    {
  "1": {
    "inputs": {
      "unet_name": "flux1-dev.safetensors",
      "weight_dtype": "default"
    },
    "class_type": "UNETLoader",
    "_meta": {
      "title": "Load Diffusion Model"
    }
  },
  "2": {
    "inputs": {
      "clip_name1": "clip_l.safetensors",
      "clip_name2": "t5xxl_fp8_e4m3fn.safetensors",
      "type": "flux",
      "device": "default"
    },
    "class_type": "DualCLIPLoader",
    "_meta": {
      "title": "DualCLIPLoader"
    }
  },
  "3": {
    "inputs": {
      "seed": 1004531644334921,
      "steps": 20,
      "cfg": 8,
      "sampler_name": "euler",
      "scheduler": "normal",
      "denoise": 1,
      "model": [
        "10",
        0
      ],
      "positive": [
        "5",
        0
      ],
      "negative": [
        "5",
        0
      ],
      "latent_image": [
        "6",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "5": {
    "inputs": {
      "text": "Luna a slim young woman, medium shot portrait, centered in frame, light brown tousled hair with natural waves, soft brown eyes, perfectly symmetrical face, ultra detailed eyes, natural skin texture, wearing oversized beige knit sweater, holding a ceramic mug with both hands, warm golden morning light on her face, gentle smile, photorealistic, ultra high detail, 8k, masterpiece, shallow depth of field, cinematic tones, cozy minimalist apartment interior, background softly blurred",
      "clip": [
        "2",
        0
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "6": {
    "inputs": {
      "width": 1024,
      "height": 1024,
      "batch_size": 1
    },
    "class_type": "EmptyLatentImage",
    "_meta": {
      "title": "Empty Latent Image"
    }
  },
  "7": {
    "inputs": {
      "samples": [
        "3",
        0
      ],
      "vae": [
        "8",
        0
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "8": {
    "inputs": {
      "vae_name": "ae.safetensors"
    },
    "class_type": "VAELoader",
    "_meta": {
      "title": "Load VAE"
    }
  },
  "9": {
    "inputs": {},
    "class_type": "PreviewImage",
    "_meta": {
      "title": "Preview Image"
    }
  },
  "10": {
    "inputs": {
      "lora_name": "merida.safetensors",
      "strength_model": 1,
      "model": [
        "1",
        0
      ]
    },
    "class_type": "LoraLoaderModelOnly",
    "_meta": {
      "title": "LoraLoaderModelOnly"
    }
  },
  "14": {
    "inputs": {
      "images": [
        "7",
        0
      ]
    },
    "class_type": "SaveImageWebsocket",
    "_meta": {
      "title": "SaveImageWebsocket"
    }
  }
}
    '''

    prompt_json = json.loads(prompt_text)
    # Set the user input on the CLIPTextEncode node (id "5")
    prompt_json["5"]["inputs"]["text"] = input
    return prompt_json

def queue_prompt(prompt):
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req =  urllib.request.Request("http://{}/prompt".format(server_address), data=data)
    return json.loads(urllib.request.urlopen(req).read())

def get_image(filename, subfolder, folder_type):
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen("http://{}/view?{}".format(server_address, url_values)) as response:
        return response.read()

def get_history(prompt_id):
    with urllib.request.urlopen("http://{}/history/{}".format(server_address, prompt_id)) as response:
        return json.loads(response.read())

def get_images(ws, prompt):
    prompt_id = queue_prompt(prompt)['prompt_id']
    output_image = None
    current_node = ""
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data['prompt_id'] == prompt_id:
                    if data['node'] is None:
                        break #Execution is done
                    else:
                        node_number = data['node']
                        current_node = prompt[node_number]["class_type"]
        else:
            if current_node == save_image_websocket:
                output_image = out[8:]

    return output_image

def fetch_image_from_comfy(input):
    ws = websocket.WebSocket()
    ws.connect("ws://{}/ws?clientId={}".format(server_address, client_id))
    images = get_images(ws, get_prompt_with_workflow(input))
    ws.close()
    return images