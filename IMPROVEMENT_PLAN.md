# 📋 DocAgent 功能性改进方案

> **生成时间**: 2026-01-05  
> **目标**: 从学术论文到生产级文档审查系统的升级路径

---

## 🎯 一、核心功能增强（⭐⭐⭐⭐⭐ 必须实现）

### 1.1 细粒度事实追踪系统（解决"张三 vs 李四"问题）

**现状问题**：
- 当前逻辑审查基于"摘要级"一致性检查
- 无法检测细节冲突（如人名、数值、日期不一致）

**改进方案**：
```python
# 在 doc_agent.py 中新增
class FactTracker:
    """关键事实追踪器，用于检测细粒度冲突"""
    
    def __init__(self):
        self.fact_store = {}  # {fact_key: [(value, chapter, page), ...]}
    
    def extract_facts(self, text, chapter_info):
        """从文本中提取关键事实"""
        prompt = """
        从以下文本中提取关键事实（人名、机构、日期、数值、实验参数等），
        输出JSON格式：
        {
          "人物": {"甲方": "张三", "乙方": "李四"},
          "时间": {"项目启动": "2023年3月"},
          "数值": {"样本数量": "1000", "准确率": "95%"},
          "参数": {"学习率": "0.001"}
        }
        """
        # 调用LLM提取事实
        facts = llm_call(prompt, text)
        return facts
    
    def check_conflicts(self):
        """检查事实冲突"""
        conflicts = []
        for fact_key, occurrences in self.fact_store.items():
            values = [occ[0] for occ in occurrences]
            if len(set(values)) > 1:  # 发现不一致
                conflicts.append({
                    "fact_key": fact_key,
                    "occurrences": occurrences,
                    "suggestion": f"事实冲突：{fact_key} 在不同章节的值不同"
                })
        return conflicts

# 集成到 run_hierarchical_logic_review
def run_hierarchical_logic_review(self):
    fact_tracker = FactTracker()  # 新增
    
    for chapter in chapters:
        # 现有的摘要提取
        summary = extract_summary(chapter)
        
        # 新增：事实提取
        facts = fact_tracker.extract_facts(chapter["content"], chapter["info"])
        for key, value in facts.items():
            fact_tracker.add(key, value, chapter["title"], chapter["page"])
    
    # Reduce阶段：检查事实冲突
    fact_conflicts = fact_tracker.check_conflicts()
    all_issues.extend(fact_conflicts)
```

**效果**：
- ✅ 可检测"第2章说甲方是张三，第6章表格说甲方是李四"
- ✅ 可检测"第3章样本1000个，第4章表格显示800个"
- ✅ 可检测日期、数值的跨章节矛盾

---

### 1.2 表格专项审查模块（高价值）

**现状问题**：
- 表格内容依赖PDF解析器，错误率高（如"，，，"乱码）
- 没有专门的表格语义审查

**改进方案**：
```python
def run_table_review(self, vision_model_id="qwen3-vl-flash"):
    """专门的表格审查：结构+内容+与正文一致性"""
    results = []
    
    for table_elem in self.doc_reader.root.iter("Table"):
        table_id = table_elem.get("table_id")
        page_num = table_elem.get("page_num")
        
        # Step 1: 用VLM重新解析表格（绕过PDF解析器）
        media_type, table_img, error = self.doc_reader.get_table_image(table_id)
        
        prompt = """
        你是表格审查专家，请检查以下方面：
        
        1. **结构规范性**：
           - 表格是否有标题（如"表 3-2 实验结果对比"）？
           - 表头是否清晰？列名是否完整？
           - 是否有必要的单位标注？
        
        2. **内容完整性**：
           - 是否有空白单元格（应填"-"或"N/A"）？
           - 数值精度是否一致（有的2位小数，有的3位）？
           - 是否有明显的数据异常（如负的时间）？
        
        3. **与正文一致性**：
           - 表格标题与正文引用（"如表3-2所示"）是否匹配？
           - 表格数据与正文描述是否一致？
        
        输出JSON：
        {
          "table_title": "从图中提取的真实标题",
          "issues": [
            {
              "issue_type": "表格规范性",
              "severity": "High|Medium|Low",
              "suggestion": "具体问题描述"
            }
          ]
        }
        """
        
        response = vision_model.call(prompt, table_img)
        results.append({
            "table_id": table_id,
            "page": page_num,
            "issues": parse_response(response)
        })
    
    return results
```

**效果**：
- ✅ 绕过PDF解析器，直接从图片分析表格
- ✅ 检查表格编号、标题、单位等规范性
- ✅ 检查表格与正文数据一致性

---

### 1.3 完整性审查模块（当前缺失）

**现状问题**：
- 代码中只有 `normative_review` 和 `logic_review`，没有实现完整性审查

**改进方案**：
```python
def run_completeness_review(self):
    """完整性审查：检查文档必需元素是否缺失"""
    outline = self.get_outline()
    body_text = self._extract_plain_text(char_limit=10000)
    
    prompt = """
    你是学术论文完整性审查员，检查以下必需元素是否存在：
    
    【本科/硕士毕业论文必需元素】：
    - [ ] 封面（标题、作者、学号、指导教师、日期）
    - [ ] 中文摘要 + 关键词
    - [ ] 英文摘要 + Keywords
    - [ ] 目录
    - [ ] 正文（引言、相关工作、方法、实验、结论）
    - [ ] 参考文献（至少15篇）
    - [ ] 致谢
    - [ ] 附录（如有代码）
    
    【学术期刊论文必需元素】：
    - [ ] 摘要 (Abstract)
    - [ ] 关键词 (Keywords)
    - [ ] 引言 (Introduction)
    - [ ] 方法 (Methods/Methodology)
    - [ ] 结果 (Results)
    - [ ] 讨论 (Discussion)
    - [ ] 结论 (Conclusion)
    - [ ] 参考文献 (References)
    
    请检查文档类型，并列出缺失的元素。
    
    输出JSON：
    {
      "document_type": "本科毕业论文|硕士论文|期刊论文",
      "missing_elements": ["中文摘要", "致谢"],
      "issues": [
        {
          "issue_type": "完整性",
          "severity": "High",  // 必需元素缺失为High
          "suggestion": "缺少中文摘要，建议补充"
        }
      ]
    }
    """
    
    return self._run_simple_review(prompt)
```

**效果**：
- ✅ 自动识别文档类型（论文、报告等）
- ✅ 检查必需章节是否缺失
- ✅ 检查摘要、关键词、参考文献等元素

---

## 🛠️ 二、工程质量提升（⭐⭐⭐⭐ 强烈推荐）

### 2.1 配置管理系统（当前缺失）

**现状问题**：
- 没有 `config.yaml`，所有配置硬编码
- 难以切换模型、调整参数

**改进方案**：
```yaml
# config.yaml
models:
  text_model:
    provider: "deepseek"  # deepseek | openai | dashscope
    model_id: "deepseek-chat"
    temperature: 0.0
    max_tokens: 8192
    api_key: "${DEEPSEEK_API_KEY}"  # 支持环境变量
    base_url: "https://api.deepseek.com"
  
  vision_model:
    provider: "dashscope"
    model_id: "qwen3-vl-flash"
    temperature: 0.0
    api_key: "${DASHSCOPE_API_KEY}"

review_settings:
  normative:
    enabled: true
    vision_verification: true  # 是否启用视觉二次校验
    max_issues: 10
  
  logic:
    enabled: true
    use_hierarchical: true  # 是否使用分层架构
    max_chapters: 8
  
  completeness:
    enabled: true
  
  vision:
    enabled: false  # 默认关闭（费时）
    max_images: 50
    include_page_context: true

performance:
  cache_enabled: true  # 缓存中间结果
  retry_on_error: 3
  timeout: 60  # 秒

output:
  format: "html"  # html | json | markdown
  include_thinking: true
  save_intermediate: false
```

```python
# config_manager.py
import yaml
import os
from typing import Any, Dict

class ConfigManager:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self._resolve_env_vars()
    
    def _resolve_env_vars(self):
        """解析 ${ENV_VAR} 格式的环境变量"""
        def resolve(value):
            if isinstance(value, str) and value.startswith("${"):
                env_var = value[2:-1]
                return os.getenv(env_var)
            elif isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            return value
        
        self.config = resolve(self.config)
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        支持点号路径访问，如 get("models.text_model.temperature")
        """
        keys = path.split(".")
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default

# 在 doc_agent.py 中使用
def __init__(self, doc_reader, config_manager: ConfigManager):
    self.config = config_manager
    self.model_id = config.get("models.text_model.model_id")
    self.temperature = config.get("models.text_model.temperature")
    # ...
```

---

### 2.2 错误处理与重试机制

**现状问题**：
- 异常处理简单，只是 `try-except` 后打印错误
- 网络波动时容易失败，没有重试

**改进方案**：
```python
# error_handler.py
import time
from functools import wraps
from typing import Callable, Any

class ReviewError(Exception):
    """审查过程异常基类"""
    pass

class ModelAPIError(ReviewError):
    """模型API调用失败"""
    pass

class ParseError(ReviewError):
    """响应解析失败"""
    pass

def retry_on_error(max_retries=3, backoff=2, exceptions=(Exception,)):
    """重试装饰器"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise ModelAPIError(f"Failed after {max_retries} attempts: {e}")
                    wait_time = backoff ** attempt
                    print(f"[Retry] Attempt {attempt+1} failed, retrying in {wait_time}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator

# 使用示例
@retry_on_error(max_retries=3, exceptions=(openai.APIError, openai.Timeout))
def _call_llm_with_retry(self, messages, **kwargs):
    """带重试的LLM调用"""
    response = self.client.chat.completions.create(
        model=self.model_id,
        messages=messages,
        **kwargs
    )
    return response

def _parse_json_robust(self, raw_content: str) -> dict:
    """增强的JSON解析，带多种兜底策略"""
    try:
        # 策略1：标准JSON解析
        return json.loads(raw_content)
    except json.JSONDecodeError:
        pass
    
    try:
        # 策略2：提取```json ... ```代码块
        match = re.search(r'```json\s*(.*?)\s*```', raw_content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except json.JSONDecodeError:
        pass
    
    try:
        # 策略3：提取第一个{ }
        start = raw_content.find("{")
        end = raw_content.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw_content[start:end])
    except json.JSONDecodeError:
        pass
    
    # 策略4：使用LLM自我修复
    repair_prompt = f"""
    以下JSON格式错误，请修复后输出：
    {raw_content[:500]}
    
    只输出修复后的JSON，不要解释。
    """
    try:
        repaired = self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "user", "content": repair_prompt}],
            max_tokens=1000,
            temperature=0.0
        )
        return json.loads(repaired.choices[0].message.content)
    except:
        raise ParseError(f"JSON解析失败且无法自动修复: {raw_content[:200]}")
```

---

### 2.3 日志系统（当前只有print）

**改进方案**：
```python
# logger.py
import logging
from datetime import datetime

class ReviewLogger:
    def __init__(self, log_file=None):
        self.logger = logging.getLogger("DocAgent")
        self.logger.setLevel(logging.DEBUG)
        
        # 控制台输出（INFO级别）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(console_handler)
        
        # 文件输出（DEBUG级别）
        if log_file:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            )
            self.logger.addHandler(file_handler)
    
    def log_review_start(self, review_type, doc_name):
        self.logger.info(f"开始 {review_type} 审查: {doc_name}")
    
    def log_chapter_review(self, chapter_num, chapter_title):
        self.logger.debug(f"正在审查章节 {chapter_num}: {chapter_title}")
    
    def log_vision_verification(self, page, is_false_positive):
        status = "误报" if is_false_positive else "属实"
        self.logger.debug(f"视觉验证 Page {page}: {status}")
    
    def log_error(self, error_msg, exception=None):
        if exception:
            self.logger.error(f"{error_msg}: {exception}", exc_info=True)
        else:
            self.logger.error(error_msg)

# 使用
logger = ReviewLogger(log_file=f"logs/review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logger.log_review_start("规范性", "bylw.pdf")
```

---

### 2.4 缓存机制（避免重复计算）

**改进方案**：
```python
# cache_manager.py
import hashlib
import pickle
from pathlib import Path

class CacheManager:
    def __init__(self, cache_dir=".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
    
    def _get_cache_key(self, func_name, *args, **kwargs):
        """生成缓存键"""
        key_str = f"{func_name}_{str(args)}_{str(sorted(kwargs.items()))}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, key):
        cache_file = self.cache_dir / f"{key}.pkl"
        if cache_file.exists():
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        return None
    
    def set(self, key, value):
        cache_file = self.cache_dir / f"{key}.pkl"
        with open(cache_file, 'wb') as f:
            pickle.dump(value, f)

# 装饰器
def cached(cache_manager: CacheManager):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = cache_manager._get_cache_key(func.__name__, *args, **kwargs)
            cached_result = cache_manager.get(cache_key)
            if cached_result is not None:
                print(f"[Cache] 使用缓存结果: {func.__name__}")
                return cached_result
            
            result = func(*args, **kwargs)
            cache_manager.set(cache_key, result)
            return result
        return wrapper
    return decorator

# 使用
cache = CacheManager()

@cached(cache)
def run_normative_review(self):
    # 如果同一个文档之前审查过，直接返回缓存结果
    ...
```

---

## 🎨 三、Prompt工程优化（⭐⭐⭐ 推荐）

### 3.1 统一语言风格（当前中英混杂）

**现状问题**：
- `system_prompt`、`actor_prompt` 等是英文
- `normative_prompt`、`logic_prompt` 是中文
- 导致模型响应语言不稳定

**改进方案**：
```python
# prompts.py 修改
system_prompt_cn = """
你是一位专业的学术文档审查助手，专注于为学术论文、技术报告等文档提供高质量的审查服务。

你的核心能力：
1. 准确理解文档结构和内容
2. 识别格式规范、逻辑一致性、完整性问题
3. 基于学术写作标准提供专业建议

工作原则：
- 客观公正，基于事实
- 建议具体可操作
- 尊重作者劳动，鼓励改进
"""

# 所有prompt统一使用中文
```

### 3.2 增加Few-Shot示例（提升准确率）

**改进方案**：
```python
normative_prompt_with_examples = """
你是一名严格的论文"格式规范"审查员。

【示例1：正确识别编号问题】
输入大纲：
<Section section_id="2">
  <Heading>第2章 相关工作</Heading>
  <Section section_id="2.1">
    <Heading>2.1 目标检测</Heading>
  </Section>
  <Section section_id="2.3">  <!-- 注意这里跳过了2.2 -->
    <Heading>2.3 卷积神经网络</Heading>
  </Section>
</Section>

输出：
{
  "issues": [
    {
      "issue_type": "规范性",
      "severity": "High",
      "quote": "2.1 目标检测 → 2.3 卷积神经网络",
      "suggestion": "第2章小节编号不连续：从2.1跳到了2.3，缺少2.2节，请检查是否遗漏或重新编号"
    }
  ]
}

【示例2：识别图表编号错误】
输入正文片段：
"...如图3.2所示，系统架构包含三个模块。图3.4展示了详细的数据流程..."

输出：
{
  "issues": [
    {
      "issue_type": "规范性",
      "severity": "Medium",
      "quote": "图3.2 → 图3.4",
      "suggestion": "图表编号不连续：从图3.2直接跳到图3.4，缺少图3.3，请检查编号顺序"
    }
  ]
}

【现在开始实际审查】：
（后续是实际的大纲和正文）
...
"""
```

### 3.3 Prompt模块化（提升可维护性）

**改进方案**：
```python
# prompts.py 重构
class PromptTemplate:
    """Prompt模板基类"""
    def __init__(self):
        self.role_description = ""
        self.task_description = ""
        self.output_format = ""
        self.constraints = ""
        self.examples = []
    
    def build(self, **kwargs) -> str:
        sections = [
            f"# 角色定义\n{self.role_description}",
            f"\n# 任务说明\n{self.task_description}",
            f"\n# 输出格式\n{self.output_format}",
            f"\n# 约束条件\n{self.constraints}"
        ]
        
        if self.examples:
            sections.append(f"\n# 示例\n" + "\n\n".join(self.examples))
        
        # 插入动态内容
        template = "\n".join(sections)
        return template.format(**kwargs)

class NormativePrompt(PromptTemplate):
    def __init__(self):
        super().__init__()
        self.role_description = "你是一名严格的论文格式规范审查员..."
        self.task_description = "检查文档的格式规范性..."
        self.output_format = '{"issues": [...]}'
        self.constraints = "1. 只关注格式，不评价内容\n2. ..."
        self.examples = [
            "【示例1】...",
            "【示例2】..."
        ]

# 使用
prompt = NormativePrompt().build(document_outline=outline, body_text=text)
```

---

## 🚀 四、高级功能扩展（⭐⭐ 可选）

### 4.1 增量审查（只审查修改部分）

**场景**：用户修改了论文后，不需要重新审查整个文档

**实现思路**：
```python
class IncrementalReviewer:
    def __init__(self, cache_dir=".review_cache"):
        self.cache_dir = Path(cache_dir)
    
    def detect_changes(self, old_doc_path, new_doc_path):
        """检测文档变化"""
        old_hash = self._compute_chapter_hashes(old_doc_path)
        new_hash = self._compute_chapter_hashes(new_doc_path)
        
        changed_chapters = []
        for chapter_id in new_hash:
            if chapter_id not in old_hash or old_hash[chapter_id] != new_hash[chapter_id]:
                changed_chapters.append(chapter_id)
        
        return changed_chapters
    
    def incremental_review(self, doc_path, changed_chapters):
        """只审查变化的章节"""
        # 加载之前的审查结果
        old_results = self._load_cache(doc_path)
        
        # 只审查changed_chapters
        new_results = {}
        for chapter_id in changed_chapters:
            new_results[chapter_id] = self.review_chapter(chapter_id)
        
        # 合并结果
        final_results = {**old_results, **new_results}
        return final_results
```

### 4.2 评分系统（量化文档质量）

```python
class QualityScorer:
    """文档质量评分器"""
    
    def compute_score(self, review_results):
        """
        计算文档质量分数（0-100）
        
        扣分规则：
        - High severity: -10分/个
        - Medium severity: -5分/个
        - Low severity: -2分/个
        """
        base_score = 100
        
        for issue in review_results["issues"]:
            severity = issue.get("severity", "Low")
            if severity == "High":
                base_score -= 10
            elif severity == "Medium":
                base_score -= 5
            else:
                base_score -= 2
        
        return max(0, base_score)
    
    def get_grade(self, score):
        """评级"""
        if score >= 90:
            return "优秀 (A)"
        elif score >= 80:
            return "良好 (B)"
        elif score >= 70:
            return "中等 (C)"
        elif score >= 60:
            return "及格 (D)"
        else:
            return "不及格 (F)"
```

### 4.3 参考文献专项审查

```python
def run_reference_review(self):
    """参考文献审查"""
    prompt = """
    你是参考文献审查专家，检查以下问题：
    
    1. **格式一致性**：
       - 所有文献格式是否一致（GB/T 7714 / APA / IEEE）？
       - 作者名、年份、标题、出版社的顺序是否正确？
    
    2. **完整性**：
       - 是否有缺失的必需字段（如年份、页码）？
       - URL是否有访问日期？
    
    3. **引用匹配**：
       - 正文引用[1]、[2]是否在参考文献列表中都能找到？
       - 是否有参考文献列出但正文未引用？
    
    4. **质量检查**：
       - 是否引用了足够的近5年文献？
       - 是否有权威来源（期刊、会议、专著）？
    
    输出JSON...
    """
    ...
```

### 4.4 数学公式审查

```python
def run_formula_review(self):
    """数学公式审查"""
    prompt = """
    检查数学公式的规范性：
    
    1. 公式编号是否连续（(1), (2), (3)）？
    2. 公式是否居中显示？
    3. 公式中的变量在首次出现时是否有定义？
    4. 公式引用（"如式(3)所示"）是否正确？
    
    输出JSON...
    """
    ...
```

---

## 📦 五、工程化改进（⭐⭐ 可选）

### 5.1 API接口（当前只有CLI）

```python
# api_server.py
from fastapi import FastAPI, UploadFile, BackgroundTasks
from pydantic import BaseModel

app = FastAPI()

class ReviewRequest(BaseModel):
    review_types: list[str]  # ["normative", "logic", "completeness"]
    options: dict

@app.post("/api/upload")
async def upload_document(file: UploadFile):
    """上传文档"""
    doc_id = save_upload(file)
    return {"doc_id": doc_id, "filename": file.filename}

@app.post("/api/review")
async def start_review(req: ReviewRequest, background_tasks: BackgroundTasks):
    """开始审查（异步）"""
    task_id = generate_task_id()
    background_tasks.add_task(run_review_task, task_id, req)
    return {"task_id": task_id}

@app.get("/api/review/status/{task_id}")
async def get_review_status(task_id: str):
    """查询进度"""
    status = get_task_status(task_id)
    return {
        "status": status["status"],  # processing | completed | failed
        "progress": status["progress"],  # 0-100
        "logs": status["logs"]
    }

@app.get("/api/review/result/{task_id}")
async def get_review_result(task_id: str):
    """获取结果"""
    result = load_result(task_id)
    return result
```

### 5.2 Web前端（配合API）

已经在之前生成的 `frontend_generation_prompt.md` 中详细说明。

### 5.3 Docker部署

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动API服务
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  docagent-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
```

---

## 🎓 六、学术价值提升（如果是论文项目）

### 6.1 对比实验设计

```python
# experiments/baseline_comparison.py

def run_ablation_study():
    """消融实验：评估各模块的贡献"""
    
    # Baseline 1: 只用文本模型（无视觉二次校验）
    baseline1_results = run_review(enable_vision_verify=False)
    
    # Baseline 2: 只用摘要级逻辑审查（无事实追踪）
    baseline2_results = run_review(enable_fact_tracking=False)
    
    # Full Model: 所有模块启用
    full_results = run_review(enable_all=True)
    
    # 计算指标
    metrics = {
        "precision": compute_precision(results),  # 准确率
        "recall": compute_recall(results),        # 召回率
        "false_positive_rate": compute_fpr(results)  # 误报率
    }
    
    # 对比表格
    comparison = pd.DataFrame({
        "Model": ["Baseline 1", "Baseline 2", "Full Model"],
        "Precision": [...],
        "Recall": [...],
        "FPR": [...]
    })
    
    return comparison
```

### 6.2 评估指标

```python
class ReviewEvaluator:
    """审查结果评估器（需要人工标注的Ground Truth）"""
    
    def __init__(self, ground_truth_path):
        self.gt = load_ground_truth(ground_truth_path)
    
    def evaluate(self, predicted_issues):
        """计算评估指标"""
        tp = 0  # True Positive: 正确检出的问题
        fp = 0  # False Positive: 误报
        fn = 0  # False Negative: 漏报
        
        for pred in predicted_issues:
            if self._is_true_issue(pred):
                tp += 1
            else:
                fp += 1
        
        for gt in self.gt:
            if not self._was_detected(gt, predicted_issues):
                fn += 1
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        
        return {
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn
        }
```

---

## 🗺️ 实施路线图

### **Phase 1: 核心功能完善（1-2周）**
- [x] 完成细粒度事实追踪
- [x] 实现完整性审查
- [x] 增加表格专项审查

### **Phase 2: 工程质量提升（1周）**
- [x] 配置管理系统
- [x] 错误处理与重试
- [x] 日志系统

### **Phase 3: Prompt优化（3天）**
- [x] 统一语言风格
- [x] 增加Few-Shot示例
- [x] Prompt模块化

### **Phase 4: 高级功能（1-2周，可选）**
- [ ] 增量审查
- [ ] 评分系统
- [ ] 参考文献审查
- [ ] 公式审查

### **Phase 5: 工程化部署（1周，可选）**
- [ ] API接口
- [ ] Web前端
- [ ] Docker部署

---

## 📊 优先级总结

| 改进项 | 优先级 | 难度 | 价值 | 实施时间 |
|-------|-------|------|------|---------|
| 事实追踪系统 | ⭐⭐⭐⭐⭐ | 中 | 极高 | 2-3天 |
| 表格专项审查 | ⭐⭐⭐⭐⭐ | 中 | 高 | 1-2天 |
| 完整性审查 | ⭐⭐⭐⭐ | 低 | 中 | 1天 |
| 配置管理 | ⭐⭐⭐⭐ | 低 | 高 | 半天 |
| 错误处理 | ⭐⭐⭐⭐ | 中 | 高 | 1天 |
| Prompt统一 | ⭐⭐⭐ | 低 | 中 | 半天 |
| 日志系统 | ⭐⭐⭐ | 低 | 中 | 半天 |
| 缓存机制 | ⭐⭐ | 中 | 中 | 1天 |
| 增量审查 | ⭐⭐ | 高 | 中 | 2-3天 |
| API接口 | ⭐⭐ | 中 | 高（如需部署）| 2-3天 |

---

## 💡 快速开始建议

如果时间有限，建议先实现以下"最小改进集"：

1. **事实追踪系统**（解决细粒度冲突检测）
2. **配置管理**（提升可维护性）
3. **完整性审查**（补全缺失模块）
4. **错误处理**（提升鲁棒性）

这4项改进可在**4-5天内完成**，能显著提升系统的**功能性、可靠性和工程质量**。

---

**生成于**: 2026-01-05  
**作者**: AI Assistant  
**项目**: DocAgent 文档审查系统

