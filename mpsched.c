#include <python3.7/Python.h>
#include <linux/tcp.h>

/*
  输入socket的文件描述符fd
  
  对socket进行一些设置
*/
static PyObject* persist_state(PyObject* self, PyObject* args)
{
  int fd;   //文件描述符
  if(!PyArg_ParseTuple(args, "i", &fd)) {
    return NULL;
  }
  int val = MPTCP_INFO_FLAG_SAVE_MASTER;
//    socket 套接字描述符
//    level 被设置的选项的级别
//    option_name 指定准备设置的选项
//    optval 指针，指向存放选项值的缓冲区
//    optlen 缓冲区长度
  setsockopt(fd, SOL_TCP, MPTCP_INFO, &val, sizeof(val)); //对套接字进行设置
  return Py_BuildValue("i", fd);
}

/*
  输入socket的文件描述符fd

  返回未确认数和重传数
*/
static PyObject* get_meta_info(PyObject* self, PyObject* args)
{
    int fd;
    if(!PyArg_ParseTuple(args, "i", &fd)) {
      return NULL;
    }

    struct mptcp_info minfo;                              // mptcp连接的相关信息
    struct mptcp_meta_info meta_info;                     // mptcp连接实例 的元信息
    struct tcp_info initial;                              // 单个tcp子连接的信息
    struct tcp_info others[NUM_SUBFLOWS];                 // 子流对象实例的集合
    struct mptcp_sub_info others_info[NUM_SUBFLOWS];      // mptcp子流信息

    minfo.tcp_info_len = sizeof(struct tcp_info);
    minfo.sub_len = sizeof(others);
    minfo.meta_len = sizeof(struct mptcp_meta_info);
    minfo.meta_info = &meta_info;
    minfo.initial = &initial;
    minfo.subflows = &others;
    minfo.sub_info_len = sizeof(struct mptcp_sub_info);
    minfo.total_sub_info_len = sizeof(others_info);
    minfo.subflow_info = &others_info;

    socklen_t len = sizeof(minfo);

    getsockopt(fd, SOL_TCP, MPTCP_INFO, &minfo, &len);
    PyObject *list = PyList_New(0);
    PyList_Append(list, Py_BuildValue("I", meta_info.mptcpi_unacked));
    PyList_Append(list, Py_BuildValue("I", meta_info.mptcpi_retransmits));
    return list;
}
/*
  传入文件描述符fd

  返回这个socket发送之后，所需要的各个子流的特征值
*/
static PyObject* get_sub_info(PyObject* self, PyObject* args)
{
  int fd;
  if(!PyArg_ParseTuple(args, "i", &fd)) {
    return NULL;
  }

// 定义一些结构体
  struct mptcp_info minfo;
  struct mptcp_meta_info meta_info;
  struct tcp_info initial;                            // tcp_info是tcp报文中的数据信息
  struct tcp_info others[NUM_SUBFLOWS];               // NUM_SUBFLOWS表示子流的数量
  struct mptcp_sub_info others_info[NUM_SUBFLOWS];    // mptcp各个子流中传输的数据报文信息

  minfo.tcp_info_len = sizeof(struct tcp_info);       // tcp报文数据信息结构体大小
  minfo.sub_len = sizeof(others);                     // 这个tcp连接子流的数量
  minfo.meta_len = sizeof(struct mptcp_meta_info);    // mptcp结构体元数据长度
  minfo.meta_info = &meta_info;                       // mptcp_info 对象中包含其自身元数据对象
  minfo.initial = &initial;                           // 数据信息
  minfo.subflows = &others;                           // 子流
  minfo.sub_info_len = sizeof(struct mptcp_sub_info); // 子流数据信息结构体大小
  minfo.total_sub_info_len = sizeof(others_info);     // 子流个数
  minfo.subflow_info = &others_info;                  // 子流传输的报文信息

  socklen_t len = sizeof(minfo);      // minfo实例的长度，使用socklen_t来标识，同int
/*
功能：获取一个套接字的选项
 参数：
     socket：文件描述符
     level：协议层次
            SOL_SOCKET 套接字层次
            IPPROTO_IP ip层次
            IPPROTO_TCP TCP层次
    option_name：选项的名称（套接字层次）
            SO_BROADCAST 是否允许发送广播信息
            SO_REUSEADDR 是否允许重复使用本地地址
           SO_SNDBUF 获取发送缓冲区长度
           SO_RCVBUF 获取接收缓冲区长度    

           SO_RCVTIMEO 获取接收超时时间
           SO_SNDTIMEO 获取发送超时时间
    option_value：获取到的选项的值
    option_len：value的长度
 返回值：
    成功：0
    失败：-1
*/
  getsockopt(fd, SOL_TCP, MPTCP_INFO, &minfo, &len);

  PyObject *list = PyList_New(0);
  int i;
  for(i=0; i < NUM_SUBFLOWS; i++){
    // 迭代所有的子流
    if(others[i].tcpi_state != 1)
      break;
    
    // 使用subflows保存各个子流的tcpi_segs_out，tcpi_rtt，tcpi_snd_cwnd
    PyObject *subflows = PyList_New(0);
    PyList_Append(subflows, Py_BuildValue("I", others[i].tcpi_segs_out));
    PyList_Append(subflows, Py_BuildValue("I", others[i].tcpi_rtt));
    PyList_Append(subflows, Py_BuildValue("I", others[i].tcpi_snd_cwnd));
    //PyList_Append(subflows, Py_BuildValue("I", others[i].tcpi_unacked));
    //PyList_Append(subflows, Py_BuildValue("I", others[i].tcpi_total_retrans)); /* Packets which are "in flight"	*/

    PyList_Append(list, subflows);
  }
  return list;
}


static PyObject* set_seg(PyObject* self, PyObject* args)
{
  PyObject * listObj;
  if (! PyArg_ParseTuple( args, "O", &listObj ))
    return NULL;

  long length = PyList_Size(listObj);
  int fd = (int)PyLong_AsLong(PyList_GetItem(listObj, 0));
  int i;

  struct mptcp_sched_info sched_info;
  sched_info.len = length-1;
  unsigned char quota[NUM_SUBFLOWS];
  unsigned char segments[NUM_SUBFLOWS];

  sched_info.quota = &quota;
  sched_info.num_segments = &segments;

  for(i=1; i<length; i++) {
    PyObject* temp = PyList_GetItem(listObj, i);
    long elem = PyLong_AsLong(temp);

    segments[i-1] = (unsigned char) elem;
  }

  setsockopt(fd, SOL_TCP, MPTCP_SCHED_INFO, &sched_info, sizeof(sched_info));

  return Py_BuildValue("i", fd);
}

static PyMethodDef Methods[] = {
  {"persist_state", persist_state, METH_VARARGS, "persist mptcp subflows tate"},
  {"get_meta_info", get_meta_info, METH_VARARGS, "get mptcp recv buff size"},
  {"get_sub_info", get_sub_info, METH_VARARGS, "get mptcp subflows info"},
  {"set_seg", set_seg, METH_VARARGS, "set num of segments in all mptcp subflows"},
  {NULL, NULL, 0, NULL}
};

static struct PyModuleDef Def = {
  PyModuleDef_HEAD_INIT,
  "mpsched",
  "mpctp scheduler \"mysched\" adjuset args",
  -1,
  Methods
};

PyMODINIT_FUNC PyInit_mpsched(void)
{
  return PyModule_Create(&Def);
}
