from __future__ import print_function

from collections import OrderedDict

import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.nn import functional as F
import os


class PropBase(object):

    def __init__(self, model, target_layer, device):
        self.model = model
        self.device = device
        self.model.to(self.device)
        self.model.eval()
        self.target_layer = target_layer
        self.outputs_backward = OrderedDict()
        self.outputs_forward = OrderedDict()
        self.set_hook_func()


    def set_hook_func(self):
        raise NotImplementedError


    def forward(self, x):
        self.preds = self.model(x)
        self.image_size = x.size(-1)
        recon_batch, self.mu, self.logvar = self.model(x)
        return recon_batch, self.mu, self.logvar

    # back prop the one_hot signal
    def backward(self, mu, logvar, mu_avg, logvar_avg):
        self.model.zero_grad()

        mu = mu.to(self.device)
        self.score_fc = torch.sum(mu)
        self.score_fc.backward(retain_graph=True)

    def get_conv_outputs(self, outputs, target_layer):
        """
        Retrieves model output for a specific module from a dictionary.
        Inputs:
            outputs - Dictionary to retrieve values from (forward or backward)
            target_layer - Specific module for which to retrieve values
        """

        # returns the gradient/f_out specified in outputs.
        # Then returns it when it is the target layer

        # outputs contains only the outputs of the one layer
        return list(outputs.values())[0]

class GradCAM(PropBase):
    # hook functions to compute gradients wrt intermediate results
    def set_hook_func(self):
        def func_b(module, grad_in, grad_out):
            """
            Hook call function that stores the backward pass gradients for every
            network module in a dictionary.
            """
            self.outputs_backward[id(module)] = grad_out[0].cpu()

        def func_f(module, input, f_output):
            """
            Hook call function that stores the forward pass output for every
            network module in a dictionary.
            """
            self.outputs_forward[id(module)] = f_output

        # Loop over all layers in the network and store outputs of forward
        # and backward passes
        for module in self.model.named_modules():
            # module[0] is name [1] is the module itself
            if module[0] == self.target_layer :
                module[1].register_backward_hook(func_b)
                module[1].register_forward_hook(func_f)

    def normalize(self, grads):
        """
        Applies L2 normalization to the gradients
        """
        l2_norm = torch.sqrt(torch.mean(torch.pow(grads, 2))) + 1e-5
        return grads / l2_norm.item()


    def compute_gradient_weights(self):
        """
        Applies the GAP operation to the gradients to obtain weights alpha.
        """
        self.grads = self.normalize(self.grads.squeeze())

        # Get height and width of attention maps
        self.map_size = self.grads.size()[2:]
        self.alpha = nn.AvgPool2d(self.map_size)(self.grads)


    def generate(self):
        """
        Generates attention map from gradients.
        """
        # Retrieve gradients of backward pass for target layer
        self.grads = self.get_conv_outputs(
            self.outputs_backward, self.target_layer)

        # compute weigths based on the gradient
        self.compute_gradient_weights()

        # Retrieve output of forward pass for target layer and set as activation
        self.activation = self.get_conv_outputs(
            self.outputs_forward, self.target_layer)




        # compute attention map for each convolution
        gcam = self.activation * self.alpha.to(self.device)
        gcam = torch.abs(gcam)

        # average the attention
        gcam = torch.mean(gcam, dim = 1)[:,None,:,:]


        # upsamples through interpolation to increase image size
        gcam = F.interpolate(gcam, (self.image_size, self.image_size),
                                mode="bilinear", align_corners=True)

        return gcam
