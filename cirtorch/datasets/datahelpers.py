import os
from PIL import Image

import torch

def cid2filename(cid, prefix):
    """
    Creates a training image path out of its CID name
    
    Arguments
    ---------
    cid      : name of the image
    prefix   : root directory where images are saved
    
    Returns
    -------
    filename : full image filename
    """
    return os.path.join(prefix, cid[-2:], cid[-4:-2], cid[-6:-4], cid)

"""
    def load_image(self, path):
        try:
            return self.transform(Image.open(path))
        except:
            # just return a black image.
            return self.transform(Image.fromarray(np.zeros((20,14,3), dtype=np.uint8)))        
"""
def pil_loader(path):
    # open path as file to avoid ResourceWarning (https://github.com/python-pillow/Pillow/issues/835)
    with open(path, 'rb') as f:
        try:
            img = Image.open(f)
        except:
            img = Image.fromarray(np.zeros((20,14,3), dtype=np.uint8))
        return img.convert('RGB')

def accimage_loader(path):
    import accimage
    try:
        return accimage.Image(path)
    except IOError:
        # Potentially a decoding problem, fall back to PIL.Image
        return pil_loader(path)

def default_loader(path):
    from torchvision import get_image_backend
    if get_image_backend() == 'accimage':
        return accimage_loader(path)
    else:
        return pil_loader(path)

def imresize(img, imsize):
    img.thumbnail((imsize, imsize), Image.ANTIALIAS)
    return img

def flip(x, dim):
    xsize = x.size()
    dim = x.dim() + dim if dim < 0 else dim
    x = x.view(-1, *xsize[dim:])
    x = x.view(x.size(0), x.size(1), -1)[:, getattr(torch.arange(x.size(1)-1, -1, -1), ('cpu','cuda')[x.is_cuda])().long(), :]
    return x.view(xsize)

def collate_tuples(batch):
    #TODO: Make it not throw gps info away
    if len(batch) == 1:
        return [batch[0][0]], [batch[0][1]], [batch[0][2]]
    return [batch[i][0] for i in range(len(batch))], [batch[i][1] for i in range(len(batch))], [batch[i][2] for i in range(len(batch))]