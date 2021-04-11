import torch
from torchvision import transforms
from torch.autograd import Variable
import torch.nn.functional as F
import torch.utils.data as Data
from torch.utils.tensorboard import SummaryWriter

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import time
import io
import PIL
import math

from cirtorch.datasets.genericdataset import ImagesFromList
from cirtorch.datasets.genericdataset import ImagesFromList
from cirtorch.networks.imageretrievalnet import init_network, extract_vectors
from cirtorch.datasets.traindataset import TuplesDataset
from cirtorch.datasets.datahelpers import collate_tuples, cid2filename

torch.manual_seed(1)

"""
PARAMS
"""
BATCH_SIZE = 1000
EPOCH = 1000

INPUT_DIM = 2048
HIDDEN_DIM1 = 1024
HIDDEN_DIM2 = 512
HIDDEN_DIM3 = 256
OUTPUT_DIM = 128 #TODO: Is this right?

LR = 0.01
WD = 4e-3

network_path = 'data/exp_outputs1/mapillary_resnet50_gem_contrastive_m0.70_adam_lr1.0e-06_wd1.0e-06_nnum5_qsize2000_psize20000_bsize5_uevery5_imsize1024/model_epoch38.pth.tar'
multiscale = '[1]'
imsize = 320

posDistThr = 25
negDistThr = 25
workers = 8
query_size = 2000
pool_size = 20000

t = time.strftime("%Y-%d-%m_%H:%M:%S", time.localtime())
tensorboard = SummaryWriter(f'data/correlation_runs/{INPUT_DIM}_{OUTPUT_DIM}_{t}')

"""
Dataset
"""
def load_placereg_net():
    # loading network from path
    if network_path is not None:
        state = torch.load(network_path)

        # parsing net params from meta
        # architecture, pooling, mean, std required
        # the rest has default values, in case that is doesnt exist
        net_params = {}
        net_params['architecture'] = state['meta']['architecture']
        net_params['pooling'] = state['meta']['pooling']
        net_params['local_whitening'] = state['meta'].get(
            'local_whitening', False)
        net_params['regional'] = state['meta'].get('regional', False)
        net_params['whitening'] = state['meta'].get('whitening', False)
        net_params['mean'] = state['meta']['mean']
        net_params['std'] = state['meta']['std']
        net_params['pretrained'] = False

        # load network
        net = init_network(net_params)
        net.load_state_dict(state['state_dict'])

        # if whitening is precomputed
        if 'Lw' in state['meta']:
            net.meta['Lw'] = state['meta']['Lw']

        print(">>>> loaded network: ")
        print(net.meta_repr())

        # setting up the multi-scale parameters
    ms = list(eval(multiscale))
    if len(ms) > 1 and net.meta['pooling'] == 'gem' and not net.meta['regional'] and not net.meta['whitening']:
        msp = net.pool.p.item()
        print(">> Set-up multiscale:")
        print(">>>> ms: {}".format(ms))
        print(">>>> msp: {}".format(msp))
    else:
        msp = 1

    # moving network to gpu and eval mode
    net.cuda()
    return net

model = load_placereg_net()
# Data loading code
print('MEAN: ' + str(model.meta['mean']))
print('STD: ' + str(model.meta['std']))

normalize = transforms.Normalize(mean=model.meta['mean'], std=model.meta['std'])
resize = transforms.Resize((int(imsize * 3/4), imsize), interpolation=2)

transform = transforms.Compose([
        resize,
        transforms.ToTensor(),
        normalize,
])

train_dataset = TuplesDataset(
        name='mapillary',
        mode='train',
        imsize=imsize,
        nnum=1,
        qsize=query_size,
        poolsize=pool_size,
        transform=transform,
        posDistThr=posDistThr,
        negDistThr=negDistThr, 
        root_dir = 'data',
        cities=''
)

val_dataset = TuplesDataset(
        name='mapillary',
        mode='val',
        imsize=imsize,
        nnum=1,
        qsize=float('Inf'),
        poolsize=float('Inf'),
        transform=transform,
        posDistThr=negDistThr, # Use 25 meters for both pos and neg
        negDistThr=negDistThr,
        root_dir = 'data',
        cities=''
)

train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=BATCH_SIZE, shuffle=True,
        num_workers=workers, pin_memory=True, sampler=None,
        drop_last=True, collate_fn=collate_tuples
)


val_loader = torch.utils.data.DataLoader(
        val_dataset, batch_size=BATCH_SIZE, shuffle=False,
        num_workers=workers, pin_memory=True,
        drop_last=True, collate_fn=collate_tuples
)

"""
NETWORK
"""
class CorrelationNet(torch.nn.Module):
    def __init__(self):
        super(CorrelationNet, self).__init__()
        self.input = torch.nn.Linear(INPUT_DIM, HIDDEN_DIM1)
        self.hidden1 = torch.nn.Linear(HIDDEN_DIM1, HIDDEN_DIM2)
        self.hidden2 = torch.nn.Linear(HIDDEN_DIM2, HIDDEN_DIM3)
        self.output = torch.nn.Linear(HIDDEN_DIM3, OUTPUT_DIM)

    def forward(self, x):
        x = F.leaky_relu(self.input(x))
        x = F.leaky_relu(self.hidden1(x))
        x = F.leaky_relu(self.hidden2(x))
        x = self.output(x)
        return x

"""
TRAINING
"""
def distance(query, positive):
    return np.linalg.norm(np.array(query)-np.array(positive))

def mse_loss(x, label, gps, eps=1e-6):
    # x is D x N
    dim = x.size(0) # D
    nq = torch.sum(label.data==-1) # number of tuples
    S = x.size(1) // nq # number of images per tuple including query: 1+1+n

    x1 = x[:, ::S].permute(1,0).repeat(1,S-1).view((S-1)*nq,dim).permute(1,0)
    idx = [i for i in range(len(label)) if label.data[i] != -1]
    x2 = x[:, idx]
    lbl = label[label!=-1]

    dif = x1 - x2
    D = torch.pow(dif+eps, 2).sum(dim=0).sqrt()

    dist = 1
    if len(gps) > 0:
        dist = distance(gps[0], gps[1])
    print(dist)
    y = lbl*torch.pow((dist - D),2)
    y = torch.sum(y)
    return y

# Network
net = CorrelationNet()
optimizer = torch.optim.Adam(net.parameters(), lr=LR, weight_decay=WD)
scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=math.exp(-0.01))
loss_func = torch.nn.MSELoss()

# Move to GPU
net = net.cuda()
loss_func = loss_func.cuda()
avg_neg_distance = train_loader.dataset.create_epoch_tuples(model)
# Train loop
losses = np.zeros(EPOCH)
for epoch in range(EPOCH):
    print(f'=>{epoch}/{EPOCH}')    
    epoch_loss = 0
    for i, (input, target, gps_info) in enumerate(train_loader):
        print(i)        
        nq = len(input) # number of training tuples
        ni = len(input[0]) # number of images per tuple
        gps_info = torch.tensor(gps_info)

        for q in range(nq):
            output = torch.zeros(OUTPUT_DIM, ni).cuda()
            for imi in range(ni):
                # compute output vector for image imi
                output[:, imi] = net(model(input[q][imi].cuda()).squeeze())
        
        loss = mse_loss(output, target[q].cuda(), gps_info[q])
        epoch_loss += loss
        print(loss, epoch_loss)
 
        loss.backward()         
 
    tensorboard.add_scalar('Loss/train', epoch_loss, epoch)

    #if (epoch % (EPOCH // 100) == 0 or (epoch == (EPOCH-1))):
    #    test(net, val_loader)

    optimizer.step()
    optimizer.zero_grad()
    scheduler.step()


def distance(query, positive):
    return np.linalg.norm(np.array(query)-np.array(positive))

def mse_loss(x, label, gps, eps=1e-6):
    # x is D x N
    dim = x.size(0) # D
    nq = torch.sum(label.data==-1) # number of tuples
    S = x.size(1) // nq # number of images per tuple including query: 1+1+n

    x1 = x[:, ::S].permute(1,0).repeat(1,S-1).view((S-1)*nq,dim).permute(1,0)
    idx = [i for i in range(len(label)) if label.data[i] != -1]
    x2 = x[:, idx]
    lbl = label[label!=-1]

    dif = x1 - x2
    D = torch.pow(dif+eps, 2).sum(dim=0).sqrt()
    
    dist = 1
    if len(gps) > 0:
        dist = distance(gps[0], gps[1])
    print(dist) 
    y = lbl*torch.pow((dist - D),2)
    y = torch.sum(y)
    return y
