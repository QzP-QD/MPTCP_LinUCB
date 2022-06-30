import threading
import time
import socket
from configparser import ConfigParser
import mpsched


import argparse
import gym
import numpy as np
from gym import wrappers
from gym import spaces

import torch
from ddpg_cnn import DDPG_CNN
from naf_cnn import NAF_CNN
from normalized_actions import NormalizedActions
from ounoise import OUNoise
from replay_memory import ReplayMemory, Transition


class io_thread(threading.Thread):

    def __init__(self, sock, filename, buffer_size):
        threading.Thread.__init__(self)
        self.sock = sock
        self.buffer_size = buffer_size
        self.filename = filename

    def run(self):
        fp = open(self.filename, 'rb')
        self.sock.send(bytes(self.filename, encoding='utf8'))
        buff = self.sock.recv(16)
        print(str(buff, encoding='utf8'))

        while(True):
            buff = fp.read(self.buffer_size)
            if not buff:
                break
            self.sock.send(buff)
        self.sock.close()
        fp.close()


class env():
    """ """
    def __init__(self, fd, buff_size, time, k, l, n, p):
        self.fd = fd
        self.buff_size = buff_size
        self.k = k  ##对以往k个时间段的观测
        self.l = l  ##吞吐量的奖励因子
        #self.m = m  ##RTT惩罚因子
        self.n = n  ##缓冲区膨胀惩罚因子
        self.p = p  ##重传惩罚因子
        self.time = time
        self.last = []
        self.tp = [[], []]
        self.rtt = [[], []]
        self.cwnd = [[], []]
        self.rr = 0
        self.count = 1
        self.recv_buff_size = 0
        
        self.observation_space = spaces.Box(np.array([0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]), np.array([float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf"),float("inf")]))
        
        self.action_space = spaces.Box(np.array([1]), np.array([4]))

    
    """ adjust info to get goodput """
    def adjust(self, state):
        for j in range(len(state)):
            self.tp[j].pop(0)
            self.tp[j].append(state[j][0]-self.last[j])
            self.rtt[j].pop(0)
            self.rtt[j].append(state[j][1])
            self.cwnd[j].pop(0)
            self.cwnd[j].append(state[j][2])
        self.last = [x[0] for x in state]
        mate = mpsched.get_meta_info(self.fd)
        self.recv_buff_size = mate[0]
        self.rr = mate[1] - self.rr
        return [self.tp[0] + self.rtt[0] + self.cwnd[0] + [self.recv_buff_size, self.rr], self.tp[1] + self.rtt[1] + self.cwnd[1]+ [self.recv_buff_size, self.rr]]

    def reward(self):
        rewards = self.l * (sum(self.tp[0]) + sum(self.tp[1]))
        #rewards = rewards - self.m * (sum(self.rtt[0]) + sum(self.rtt[1]))
        rewards = rewards + self.n * self.recv_buff_size
        rewards = rewards - self.p * self.rr
        return rewards

    """ reset env, return the initial state  """
    def reset(self):
        mpsched.persist_state(self.fd)
        time.sleep(1)
        self.last = [x[0] for x in mpsched.get_sub_info(self.fd)]

        for i in range(self.k):
            subs = mpsched.get_sub_info(self.fd)
            for j in range(len(subs)):
                 self.tp[j].append(subs[j][0]-self.last[j])
                 self.rtt[j].append(subs[j][1])
                 self.cwnd[j].append(subs[j][2])
            self.last = [x[0] for x in subs]
            time.sleep(self.time)
        mate = mpsched.get_meta_info(self.fd)
        self.recv_buff_size = mate[0]
        self.rr = mate[1]
        return [self.tp[0] + self.rtt[0] + self.cwnd[0] + [self.recv_buff_size, self.rr], self.tp[1] + self.rtt[1] + self.cwnd[1]+ [self.recv_buff_size, self.rr]]

    """ action = [sub1_buff_size, sub2_buff_size] """
    def step(self, action):
        # A = [self.fd, action[0], action[1]]
        # mpsched.set_seg(A)
        time.sleep(self.time)
        state_nxt = mpsched.get_sub_info(self.fd)
        done = False
        if len(state_nxt) == 0:
            done = True
        self.count = self.count + 1
        return self.adjust(state_nxt), self.reward(), self.count, self.recv_buff_size, done


def main():
    cfg = ConfigParser()
    cfg.read('config.ini')

    IP = cfg.get('server', 'ip')
    PORT = cfg.getint('server', 'port')
    FILE = cfg.get('file', 'file')
    SIZE = cfg.getint('env', 'buffer_size')
    TIME = cfg.getfloat('env', 'time')
    EPISODE = cfg.getint('env', 'episode')

    parser = argparse.ArgumentParser(description='PyTorch REINFORCE example')

    parser.add_argument('--gamma', type=float, default=0.99, metavar='G',
                    help='discount factor for reward (default: 0.99)')
    parser.add_argument('--tau', type=float, default=0.001, metavar='G',
                    help='discount factor for model (default: 0.001)')
                    
    parser.add_argument('--noise_scale', type=float, default=0.3, metavar='G',
                    help='initial noise scale (default: 0.3)')
    parser.add_argument('--final_noise_scale', type=float, default=0.3, metavar='G',
                    help='final noise scale (default: 0.3)')      
    parser.add_argument('--exploration_end', type=int, default=100, metavar='N',
                    help='number of episodes with noise (default: 100)')
                    
    parser.add_argument('--hidden_size', type=int, default=128, metavar='N',
                    help='number of hidden size (default: 128)')
    parser.add_argument('--replay_size', type=int, default=1000000, metavar='N',
                    help='size of replay buffer (default: 1000000)')
    parser.add_argument('--updates_per_step', type=int, default=5, metavar='N',
                    help='model updates per simulator step (default: 5)')
    parser.add_argument('--batch_size', type=int, default=64, metavar='N',
                    help='batch size (default: 128)')


    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((IP, PORT))
    fd = sock.fileno()
    my_env = env(fd=fd, buff_size=SIZE, time=TIME, k=8, l=0.01, n=0.03, p=0.05)
    mpsched.persist_state(fd)

    args = parser.parse_args()
    agent = NAF_CNN(args.gamma, args.tau, args.hidden_size,
                      my_env.observation_space.shape[0], my_env.action_space)
    memory = ReplayMemory(args.replay_size)
    ounoise = OUNoise(my_env.action_space.shape[0])

    rewards = []
    times = []
    for i_episode in range(EPISODE):
        if (i_episode < 0.9*EPISODE):  # training
            io = io_thread(sock=sock, filename=FILE, buffer_size=SIZE)
            io.start()
            
            state=my_env.reset()
            
            ounoise.scale = (args.noise_scale - args.final_noise_scale) * max(0, args.exploration_end - i_episode) / args.exploration_end + args.final_noise_scale
            ounoise.reset()
            print(state)
            episode_reward = 0
            while True:
                state = torch.FloatTensor(state)
                #print("state: {}\n ounoise: {}".format(state, ounoise.scale))
                action = agent.select_action(state, ounoise)
                #print("action: {}".format(action))
                next_state, reward, count, recv_buff_size, done = my_env.step(action)
                #print("buff size: ",recv_buff_size)
                #print("reward: ", reward)
                episode_reward += reward
                
                action = torch.FloatTensor(action)
                mask = torch.Tensor([not done])
                next_state = torch.FloatTensor(next_state)
                reward = torch.FloatTensor([float(reward)]) 
                memory.push(state, action, mask, next_state, reward)
                
                state = next_state

                if len(memory) > args.batch_size * 5:
                    for _ in range(args.updates_per_step):
                        transitions = memory.sample(args.batch_size)
                        batch = Transition(*zip(*transitions))
                        #print("update",10*'--')
                        agent.update_parameters(batch)
                    
                if done:
                    break
            rewards.append(episode_reward)
            io.join()
        else:  # testing
            io = io_thread(sock=sock, filename=FILE, buffer_size=SIZE)
            io.start()
            state=my_env.reset()
            episode_reward = 0
            start_time = time.time()
            while True:
                state = torch.FloatTensor(state)
                #print("state: {}\n".format(state))
                action = agent.select_action(state)
                #print("action: {}".format(action))
                next_state, reward, count, done = my_env.step(action)
                episode_reward += reward
                state = next_state

                if done:
                    break
            rewards.append(episode_reward)
            times.append(str(time.time() - start_time) + "\n")
            io.join()
        #print("Episode: {}, noise: {}, reward: {}, average reward: {}".format(i_episode, ounoise.scale, rewards[-1], np.mean(rewards[-100:])))
        fo = open("times.txt", "w")
        fo.writelines(lines)
        fo.close()
            
    sock.close()


if __name__ == '__main__':
    main()
